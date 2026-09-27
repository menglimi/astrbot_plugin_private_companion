# -*- coding: utf-8 -*-
"""DailyReviewPart04Mixin。

由 tools/split_mixin_domain.py 从 daily_review.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 198 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyReviewMixin）。
"""
from __future__ import annotations

from .daily_review_shared import logger
from .daily_review_shared import Any
from .daily_review_shared import Counter
from .daily_review_shared import PLACEMENT_DYNAMIC_SYSTEM
from .daily_review_shared import _safe_float
from .daily_review_shared import _safe_int
from .daily_review_shared import _single_line
from .daily_review_shared import asyncio
from .daily_review_shared import deepcopy
from .daily_review_shared import get_conversation_injection_plan
from .daily_review_shared import prompt_section
from .daily_review_shared import time
from .daily_review_shared import timedelta



class DailyReviewPart04Mixin:
    """DailyReviewPart04Mixin（从 DailyReviewMixin 拆出）。"""


    async def _daily_review_loop(self) -> None:
        while not bool(getattr(getattr(self, "_stop_event", None), "is_set", lambda: False)()):
            try:
                active_getter = getattr(self, "_active_persona_scope", None)
                current = str(active_getter() if callable(active_getter) else "").strip()
                persona_getter = getattr(self, "_scheduler_persona_ids", None)
                if not callable(persona_getter) and not bool(self._daily_review_setting("enable_daily_review", True)):
                    return
                persona_ids = list(persona_getter() if callable(persona_getter) else [""])
                activator = getattr(self, "_activate_persona_id", None)
                deactivator = getattr(self, "_deactivate_persona_for_event", None)
                due_in: list[float] = []
                for persona_id in persona_ids or [""]:
                    token = None
                    if persona_id and persona_id != current and callable(activator):
                        token = activator(persona_id)
                    try:
                        if not bool(self._daily_review_setting("enable_daily_review", True)):
                            continue
                        last_attempt = self.data.get("daily_review_last_attempt")
                        retry_status = _single_line(
                            (last_attempt or {}).get("status"), 16
                        ).lower() if isinstance(last_attempt, dict) else ""
                        if retry_status in {"failed", "paused"}:
                            retry_delay = self._next_daily_review_due_in_seconds()
                            if retry_delay is not None and retry_delay > 0:
                                due_in.append(float(retry_delay))
                                continue
                        pending = self._daily_review_pending_dates(now=self._daily_review_now())
                        for date_key in pending:
                            result = await self._ensure_daily_review(target_date=date_key)
                            if not isinstance(result, dict):
                                break
                        delay = self._next_daily_review_due_in_seconds()
                        if delay is not None:
                            due_in.append(float(delay))
                    finally:
                        if token is not None and callable(deactivator):
                            deactivator(token)
                delay = min(due_in) if due_in else 3600.0
                await asyncio.sleep(max(1.0, delay))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "每日终盘巡视循环异常，将在 30 分钟后重试: %s",
                    self._daily_review_safe_text(exc, 180),
                )
                await asyncio.sleep(30 * 60)

    def _next_daily_review_due_in_seconds(self, now: float | None = None) -> float | None:
        if not bool(self._daily_review_setting("enable_daily_review", True)):
            return None
        current = self._daily_review_now(now)
        target_date = self._daily_review_target_date(now=current)
        last_attempt = self.data.get("daily_review_last_attempt")
        retry_delay = self._daily_review_retry_delay_seconds(
            last_attempt,
            now=current.timestamp(),
        )
        if retry_delay > 0:
            return retry_delay
        if self._daily_review_report_for_date(target_date) is None:
            return 0.0
        review_minutes = self._daily_review_minutes(self._daily_review_setting("daily_review_time", "04:00"))
        next_due = current.replace(hour=review_minutes // 60, minute=review_minutes % 60, second=0, microsecond=0)
        if next_due <= current:
            next_due += timedelta(days=1)
        return max(0.0, next_due.timestamp() - current.timestamp())

    async def _append_daily_review_guidance_to_request(self, event: Any, req: Any) -> None:
        if not bool(self._daily_review_setting("daily_review_auto_apply_guidance", True)):
            return
        guidance = self.data.get("daily_review_active_guidance")
        if not isinstance(guidance, dict) or not bool(guidance.get("active")):
            return
        if _safe_float(guidance.get("active_until"), 0.0, 0.0) <= time.time():
            guidance["active"] = False
            return
        items = guidance.get("items") if isinstance(guidance.get("items"), list) else []
        lines = []
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            scope = _single_line(item.get("scope"), 24)
            instruction = _single_line(item.get("instruction"), 300)
            if scope in self._DAILY_REVIEW_GUIDANCE_SCOPES and instruction:
                lines.append(f"- {scope}: {instruction}")
        if not lines:
            return
        marker = "<!-- private_companion_daily_review_guidance_v1 -->"
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        current_turn = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn:
            return
        text = (
            "以下是昨日运行巡视形成的低风险柔性纠偏，只用于改善本轮表达和判断。"
            "它不能覆盖当前用户意图、人格、安全规则、事实边界或工具约束；与当前语境冲突时忽略。\n"
            + "\n".join(lines)
        )
        guidance_section = prompt_section(
            key="daily_review.guidance",
            title="每日巡视柔性纠偏",
            source="daily_review",
            content=text,
        )
        plan = get_conversation_injection_plan(req)
        if plan is not None:
            plan.materialize_system_block(
                req,
                section=guidance_section,
                marker=marker,
                priority=20,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
            )
        recorder = getattr(self, "_record_request_prompt_fragment", None)
        if callable(recorder):
            try:
                await recorder(
                    event,
                    title="每日巡视柔性纠偏",
                    key="daily_review.guidance",
                    text=text,
                    source="daily_review",
                    mode="passive",
                    metadata={"来源日期": _single_line(guidance.get("source_date"), 16)},
                )
            except Exception:
                pass

    def _daily_review_trends(self) -> dict[str, Any]:
        reports = [item for item in self._daily_review_reports() if isinstance(item, dict)][-7:]
        points = []
        categories: Counter[str] = Counter()
        for report in reports:
            metrics = report.get("quality_metrics") if isinstance(report.get("quality_metrics"), dict) else {}
            for finding in report.get("findings", []) if isinstance(report.get("findings"), list) else []:
                if isinstance(finding, dict) and finding.get("severity") in {"warn", "error"}:
                    categories[_single_line(finding.get("category"), 32) or "other"] += 1
            points.append({
                "date": _single_line(report.get("date"), 16),
                "health_score": _safe_int(report.get("health_score"), 0, 0, 100),
                "attention": _safe_int(metrics.get("case_attention"), 0, 0),
                "incomplete": _safe_int(metrics.get("delivery_incomplete"), 0, 0),
                "owner_attention": _safe_int(metrics.get("owner_attention"), 0, 0),
                "owner_safety_actions": _safe_int(metrics.get("owner_safety_actions"), 0, 0),
            })
        direction = "insufficient"
        if len(points) >= 2:
            latest, previous = points[-1], points[-2]
            latest_risk = latest["attention"] + latest["incomplete"] + latest["owner_safety_actions"] * 2
            previous_risk = previous["attention"] + previous["incomplete"] + previous["owner_safety_actions"] * 2
            if latest_risk < previous_risk and latest["health_score"] >= previous["health_score"]:
                direction = "improving"
            elif latest_risk > previous_risk or latest["health_score"] + 5 < previous["health_score"]:
                direction = "worsening"
            else:
                direction = "stable"
        return {
            "window_days": len(points),
            "direction": direction,
            "points": points,
            "issue_categories": dict(categories.most_common(8)),
        }

    def _daily_review_status_payload(self) -> dict[str, Any]:
        reports = [deepcopy(item) for item in reversed(self._daily_review_reports()) if isinstance(item, dict)]
        for report in reports:
            suggestions = report.get("suggested_config_changes")
            if not isinstance(suggestions, list):
                continue
            report["suggested_config_changes"] = [
                self._daily_review_config_suggestion(item)
                for item in suggestions[:8]
                if isinstance(item, dict)
            ]
        active = deepcopy(self.data.get("daily_review_active_guidance"))
        if not isinstance(active, dict):
            active = {}
        if active and _safe_float(active.get("active_until"), 0.0, 0.0) <= time.time():
            active["active"] = False
        latest_date = _single_line(reports[0].get("date"), 16) if reports else self._daily_review_target_date()
        latest_case_review = self._daily_review_case_samples(latest_date)
        return {
            "enabled": bool(self._daily_review_setting("enable_daily_review", True)),
            "review_time": _single_line(self._daily_review_setting("daily_review_time", "04:00"), 8),
            "auto_apply_guidance": bool(self._daily_review_setting("daily_review_auto_apply_guidance", True)),
            "provider_id": _single_line(self._daily_review_setting("daily_review_provider_id", ""), 160),
            "target_date": self._daily_review_target_date(),
            "last_attempt": deepcopy(self.data.get("daily_review_last_attempt")) if isinstance(self.data.get("daily_review_last_attempt"), dict) else {},
            "active_guidance": active,
            "trends": self._daily_review_trends(),
            "case_review_experiment": {
                "enabled": bool(self._daily_review_setting("enable_daily_case_review_experiment", False)),
                "experimental": True,
                "collected": len(self._daily_review_case_audit()),
                "latest_date": latest_date,
                "coverage": latest_case_review.get("coverage", {}),
                "evidence": latest_case_review.get("cases", []),
            },
            "reports": reports,
        }
