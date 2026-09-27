# -*- coding: utf-8 -*-
"""setup_generation 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 300 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from .helpers import _today_key
from copy import deepcopy
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiSetupGenerationMixin:
    """setup_generation 域（从 PrivateCompanionPageApi 拆出）。"""


    async def run_setup_daily_generation(self) -> dict[str, Any]:
        """Run setup-guide daily plan generation; current detail is optional because it is slow."""
        try:
            payload = await request.get_json(silent=True) or {}
        except Exception:
            payload = {}
        generate_schedule = self._normalize_bool_value(payload.get("generate_schedule", True))
        refine_schedule = self._normalize_bool_value(payload.get("refine_schedule", True))
        force_detail = self._normalize_bool_value(payload.get("force_detail", False))
        timeout_seconds = self._int(payload.get("timeout_seconds"), 18, 3, 60)
        plan: dict[str, Any] | None = None
        detail: dict[str, Any] | None = None
        current_detail_text = ""
        generation_status = ""
        pending = False
        detail_pending = False
        detail_error = ""
        try:
            if generate_schedule:
                plan, generation_status, pending = await self._setup_guide_generate_daily_plan_fast(timeout=timeout_seconds)
            else:
                async with self.plugin._data_lock:
                    current_plan = self.plugin.data.get("daily_plan", {})
                    plan = dict(current_plan) if isinstance(current_plan, dict) else {}
                generation_status = "current"

            if refine_schedule:
                if not plan:
                    plan = await self.plugin._ensure_daily_plan(force=False)
                    if not plan:
                        plan = await self.plugin._ensure_daily_plan(force=True)
                detail_refiner = getattr(self.plugin, "_ensure_detail_enhancement", None)
                if callable(detail_refiner):
                    detail_timeout = max(3, min(60, timeout_seconds))
                    detail_task = self._create_page_background_task(
                        detail_refiner(force=bool(force_detail)),
                        label="setup_daily_detail",
                    )
                    if detail_task is None:
                        detail_error = "后台细化任务无法启动"
                        detail_pending = False
                        detail_task = None

                    def _consume_setup_detail_task(done_task: asyncio.Task) -> None:
                        try:
                            done_task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "首次配置后台日程细化失败: %s",
                                self._single_line(exc, 180),
                                exc_info=True,
                            )

                    if detail_task is not None:
                        detail_task.add_done_callback(_consume_setup_detail_task)
                    try:
                        if detail_task is None:
                            raise RuntimeError("setup detail task unavailable")
                        detail = await asyncio.wait_for(asyncio.shield(detail_task), timeout=detail_timeout)
                    except asyncio.TimeoutError:
                        detail_pending = True
                        detail_error = f"当前细化超过 {detail_timeout}s，已转入后台继续生成，可先继续配置。"
                    except Exception as exc:
                        detail_error = f"当前细化失败：{self._single_line(exc, 160)}"
                        logger.warning(
                            "首次配置日程细化失败: %s",
                            self._single_line(exc, 180),
                            exc_info=True,
                        )

            formatter = getattr(self.plugin, "_format_current_detail_view", None)
            if callable(formatter):
                try:
                    current_detail_text = formatter()
                except Exception as exc:
                    current_detail_text = f"当前细化展示失败：{self._single_line(exc, 160)}"

            async with self.plugin._data_lock:
                data = self._overview_data_snapshot_locked(self.plugin.data)

            plan_payload = dict(plan) if isinstance(plan, dict) else {}
            if not isinstance(plan_payload.get("items"), list) and isinstance(plan_payload.get("schedule"), list):
                plan_payload["items"] = plan_payload.get("schedule")

            return self._ok(
                {
                    "ok": True,
                    "plan": plan_payload,
                    "detail": detail if isinstance(detail, dict) else {},
                    "current_detail_text": current_detail_text,
                    "daily_state": self._daily_state_summary(data.get("daily_state")),
                    "daily_timeline": self._daily_timeline_summary(data),
                    "generation_status": generation_status,
                    "pending": pending,
                    "detail_pending": detail_pending,
                    "detail_error": detail_error,
                    "detail_skipped": bool(generate_schedule and not refine_schedule),
                }
            )
        except Exception as exc:
            logger.warning(f"首次配置日程生成失败: {exc}", exc_info=True)
            return self._ok({"ok": False, "error": self._single_line(exc, 220)})

    async def regenerate_daily_detail_segment(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
        except Exception:
            payload = {}
        key = self._single_line(payload.get("key"), 120)
        if not key:
            return self._error("缺少要重生成的时间段")
        action = self._single_line(payload.get("action"), 24).lower() or "regenerate"
        if action not in {"regenerate", "cancel"}:
            return self._error("不支持的日程段操作")
        previous_snapshot: dict[str, Any] = {}
        previous_item_state: dict[str, tuple[bool, Any]] = {}
        generation_id = ""
        segment: dict[str, Any] = {}
        try:
            async with self.plugin._data_lock:
                plan = deepcopy(self.plugin.data.get("daily_plan", {}))
                state = deepcopy(self.plugin.data.get("daily_state", {}))
                segments = self.plugin._collect_detail_segments(plan, {}, include_cancelled=True)
                segment = next((item for item in segments if self._single_line(item.get("key"), 120) == key), None)
                if not isinstance(segment, dict):
                    return self._error("该时间段已不存在或不属于今天的日程")
                live_plan = self.plugin.data.get("daily_plan", {})
                live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                live_index = self._int(segment.get("index"), -1, -1)
                live_item = live_items[live_index] if isinstance(live_items, list) and 0 <= live_index < len(live_items) and isinstance(live_items[live_index], dict) else None
                self.plugin._sync_detail_enhancement_day_locked(plan.get("date"))
                enhanced = self.plugin.data.setdefault("detail_enhanced_segments", {})
                if not isinstance(enhanced, dict):
                    enhanced = {}
                    self.plugin.data["detail_enhanced_segments"] = enhanced
                previous_snapshot = deepcopy(enhanced.get(key)) if isinstance(enhanced.get(key), dict) else {}
                if (
                    action == "regenerate"
                    and self._single_line(previous_snapshot.get("status"), 24) == "generating"
                    and self.plugin._detail_enhancement_snapshot_blocks_generation(previous_snapshot)
                ):
                    return self._error("该时间段正在细化中，请等待当前生成完成后再试")
                if action == "cancel":
                    if isinstance(live_item, dict):
                        live_item["lifecycle_status"] = "cancelled"
                        live_item["changed_at"] = self.plugin._environment_now().strftime("%H:%M")
                        live_item["change_reason"] = "用户在陪伴面板取消该日程段"
                        live_item.pop("_detail_generation_id", None)
                    cancelled = previous_snapshot or {"status": "done", "summary": "这一段已取消。", "today_events": [], "proactive_events": [], "state_variables": []}
                    for event in list(cancelled.get("today_events") or []) + list(cancelled.get("proactive_events") or []):
                        if isinstance(event, dict):
                            event["lifecycle_status"] = "cancelled"
                    cancelled["status"] = "cancelled"
                    cancelled["summary"] = self._single_line(cancelled.get("summary"), 120) or "这一段已取消。"
                    cancelled["cancelled_at"] = self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    cancelled.pop("generation_id", None)
                    cancelled.pop("previous_item_state", None)
                    cancelled.pop("retry_after", None)
                    cancelled.pop("retry_after_ts", None)
                    enhanced[key] = cancelled
                    story = self.plugin._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                    self.plugin._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), enhanced, story)
                    self.plugin._save_data_sync(
                        sections={
                            "daily_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                            "detail_enhanced_history",
                            "daily_story_plan",
                            "daily_story_plan_history",
                        }
                    )
                    data = self._overview_data_snapshot_locked(self.plugin.data)
                    return self._ok({"key": key, "cancelled": True, "daily_timeline": self._daily_timeline_summary(data)})
                if isinstance(live_item, dict):
                    for field in ("lifecycle_status", "changed_at", "change_reason", "_detail_generation_id"):
                        previous_item_state[field] = (field in live_item, deepcopy(live_item.get(field)))
                    generation_id = uuid.uuid4().hex
                    live_item["lifecycle_status"] = "changed"
                    live_item["changed_at"] = self.plugin._environment_now().strftime("%H:%M")
                    live_item["change_reason"] = "用户在陪伴面板重新细化该日程段"
                    live_item["_detail_generation_id"] = generation_id
                    segment_item = segment.get("item") if isinstance(segment.get("item"), dict) else None
                    if isinstance(segment_item, dict):
                        segment_item["lifecycle_status"] = "changed"
                else:
                    generation_id = uuid.uuid4().hex
                enhanced[key] = {
                    "status": "generating",
                    "started_at": self.plugin._environment_now().strftime("%H:%M"),
                    "started_ts": time.time(),
                    "regenerated": True,
                    "generation_id": generation_id,
                    "previous_item_state": {
                        field: {"existed": existed, "value": value}
                        for field, (existed, value) in previous_item_state.items()
                    },
                }
                self.plugin._save_data_sync(
                    sections={
                        "daily_plan",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                    }
                )

            detail = await _page_api_host.generate_detail_enhancement(self.plugin, segment, plan, state)
            if not isinstance(detail.get("today_events"), list) or not detail.get("today_events"):
                raise RuntimeError("局部重生成未返回可用的细化事件")

            async with self.plugin._data_lock:
                if not self.plugin._detail_generation_is_current(segment, generation_id):
                    stale_plan = self.plugin.data.get("daily_plan", {})
                    stale_items = stale_plan.get("items") if isinstance(stale_plan, dict) else None
                    stale_index = self._int(segment.get("index"), -1, -1)
                    stale_item = stale_items[stale_index] if isinstance(stale_items, list) and 0 <= stale_index < len(stale_items) and isinstance(stale_items[stale_index], dict) else None
                    if isinstance(stale_item, dict) and self._single_line(stale_item.get("_detail_generation_id"), 64) == generation_id:
                        stale_item.pop("_detail_generation_id", None)
                        self.plugin._save_data_sync(
                            sections={
                                "daily_plan",
                                "detail_enhanced_day",
                                "detail_enhanced_segments",
                            }
                        )
                    return self._error("该时间段已被取消、替换或由更新的操作接管，本次迟到结果未写入")
                current = self.plugin.data.setdefault("detail_enhanced_segments", {})
                current[key] = {
                    "status": "done",
                    "updated_at": self.plugin._environment_now().strftime("%H:%M"),
                    "summary": self._single_line(detail.get("summary"), 120),
                    "summary_basis": self.plugin._normalize_schedule_basis(detail.get("summary_basis"), default=["coarse_plan"]),
                    "summary_confidence": min(1.0, self._float(detail.get("summary_confidence"), 0.75)),
                    "location": self._single_line(detail.get("location"), 60),
                    "location_basis": self.plugin._normalize_schedule_basis(detail.get("location_basis"), default=["coarse_plan"]),
                    "location_confidence": min(1.0, self._float(detail.get("location_confidence"), 0.72)),
                    "today_events": detail.get("today_events", []),
                    "proactive_events": detail.get("proactive_events", []),
                    "state_variables": detail.get("state_variables", []),
                    "presence_status": detail.get("presence_status", {}),
                    "quality": detail.get("quality", {}),
                    "interaction_updates": previous_snapshot.get("interaction_updates", []),
                    "regenerated": True,
                    "regenerated_at": self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.plugin._sanitize_detail_enhanced_segments_inplace(current)
                story = self.plugin._rebuild_story_plan_from_detail_snapshots(str(plan.get("date") or _today_key()))
                self.plugin._remember_detail_enhancement_history(str(plan.get("date") or _today_key()), current, story)
                current_plan = self.plugin.data.get("daily_plan", {})
                current_items = current_plan.get("items") if isinstance(current_plan, dict) else None
                current_index = self._int(segment.get("index"), -1, -1)
                current_item = current_items[current_index] if isinstance(current_items, list) and 0 <= current_index < len(current_items) and isinstance(current_items[current_index], dict) else None
                if isinstance(current_item, dict) and self._single_line(current_item.get("_detail_generation_id"), 64) == generation_id:
                    current_item.pop("_detail_generation_id", None)
                self.plugin._refresh_daily_state_location_from_plan(
                    plan=current_plan if isinstance(current_plan, dict) else plan,
                    detail=detail,
                    segment=segment,
                )
                self.plugin._save_data_sync(
                    sections={
                        "daily_plan",
                        "daily_state",
                        "detail_enhanced_day",
                        "detail_enhanced_segments",
                        "detail_enhanced_history",
                        "daily_story_plan",
                        "daily_story_plan_history",
                    }
                )
                data = self._overview_data_snapshot_locked(self.plugin.data)
            return self._ok({"key": key, "detail": detail, "daily_timeline": self._daily_timeline_summary(data)})
        except Exception as exc:
            logger.warning("局部重生成日程细化失败: %s", exc, exc_info=True)
            async with self.plugin._data_lock:
                enhanced = self.plugin.data.setdefault("detail_enhanced_segments", {})
                if isinstance(enhanced, dict) and key and generation_id and self.plugin._detail_generation_is_current(segment, generation_id):
                    restored = previous_snapshot or {"status": "failed", "today_events": [], "proactive_events": [], "state_variables": []}
                    restored["regeneration_error"] = self._single_line(exc, 180)
                    restored["regeneration_failed_at"] = self.plugin._environment_now().strftime("%Y-%m-%d %H:%M:%S")
                    enhanced[key] = restored
                    live_plan = self.plugin.data.get("daily_plan", {})
                    live_items = live_plan.get("items") if isinstance(live_plan, dict) else None
                    live_index = self._int(segment.get("index"), -1, -1)
                    live_item = live_items[live_index] if isinstance(live_items, list) and 0 <= live_index < len(live_items) and isinstance(live_items[live_index], dict) else None
                    if isinstance(live_item, dict) and self._single_line(live_item.get("_detail_generation_id"), 64) == generation_id:
                        for field, (existed, value) in previous_item_state.items():
                            if existed:
                                live_item[field] = value
                            else:
                                live_item.pop(field, None)
                    self.plugin._save_data_sync(
                        sections={
                            "daily_plan",
                            "detail_enhanced_day",
                            "detail_enhanced_segments",
                        }
                    )
            return self._exception_error("局部重生成失败")
