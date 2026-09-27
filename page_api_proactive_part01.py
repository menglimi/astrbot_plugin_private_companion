# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiProactivePart01Mixin。

由 tools/split_mixin_domain.py 从 page_api_proactive.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 454 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiProactiveMixin）。
"""
from __future__ import annotations

from .page_api_proactive_shared import logger
from .page_api_proactive_shared import Any
from .page_api_proactive_shared import deepcopy
from .page_api_proactive_shared import request
from .page_api_proactive_shared import secrets
from .page_api_proactive_shared import time



class PrivateCompanionPageApiProactivePart01Mixin:
    """PrivateCompanionPageApiProactivePart01Mixin（从 PrivateCompanionPageApiProactiveMixin 拆出）。"""


    async def update_proactive_only_unlock(self) -> dict[str, Any]:
        try:
            payload = await request.get_json(silent=True) or {}
            key = self._single_line(payload.get("key"), 80)
            action = self._single_line(payload.get("action"), 20) or "unlock"
            sync_related = bool(payload.get("sync_related"))
            normalizer = getattr(self.plugin, "_normalize_proactive_only_unlock_key", None)
            normalized = normalizer(key) if callable(normalizer) else key
            if not normalized:
                return self._error("缺少要临时放行的功能")
            applier = getattr(self.plugin, "_apply_proactive_only_temp_unlock", None)
            if not callable(applier):
                return self._error("当前插件缺少主动专用模式临时放行接口")
            clear = action in {"clear", "remove", "cancel", "关闭", "取消"}
            message = applier(normalized, sync_related=sync_related, clear=clear)
            return self._ok(
                {
                    "message": message,
                    "proactive_only": self._proactive_only_mode_snapshot(),
                }
            )
        except Exception as exc:
            logger.error(f"更新主动专用临时放行失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def delete_proactive_candidate(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        candidate_id = self._single_line(payload.get("candidate_id") or payload.get("id"), 40)
        if not candidate_id:
            return self._error("缺少 candidate_id")
        try:
            async with self.plugin._data_lock:
                raw = self.plugin.data.get("proactive_candidate_pool")
                if not isinstance(raw, list):
                    raw = []
                    self.plugin.data["proactive_candidate_pool"] = raw
                removed_item = None
                kept = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    if self._single_line(item.get("id"), 40) == candidate_id and removed_item is None:
                        removed_item = dict(item)
                        continue
                    kept.append(item)
                if removed_item is None:
                    return self._error("没有找到对应主动候选")
                self.plugin.data["proactive_candidate_pool"] = kept
                user_id = self._single_line(removed_item.get("user_id"), 40)
                users = self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {}
                cleared_current_plan = False
                if user_id and isinstance(users.get(user_id), dict):
                    user = users[user_id]
                    if self._single_line(user.get("planned_candidate_id"), 40) == candidate_id:
                        clearer = getattr(self.plugin, "_clear_pending_proactive_plan", None)
                        scheduler = getattr(self.plugin, "_schedule_next_proactive", None)
                        if callable(clearer):
                            clearer(user)
                            cleared_current_plan = True
                        if callable(scheduler):
                            scheduler(user, now=time.time())
                    shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                    if callable(shrinker):
                        shrinker(user_id, note="page_delete")
                self.plugin._save_data_sync(
                    sections={"users", "proactive_candidate_pool"}
                )
                data = self._overview_data_snapshot_locked(self.plugin.data)
            message = "已删除主动候选"
            if cleared_current_plan:
                message += "，并重新安排了下一次主动检查"
            proactive_tasks = await self._proactive_task_summary_async(
                data,
                force_refresh=True,
            )
            return self._ok(
                {
                    "message": message,
                    "removed": True,
                    "cleared_current_plan": cleared_current_plan,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": proactive_tasks,
                }
            )
        except Exception as exc:
            logger.error(f"删除主动候选失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def prune_proactive_candidates(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        user_id = self._single_line(payload.get("user_id"), 40)
        if not user_id:
            return self._error("缺少 user_id")
        keep = self._int(payload.get("keep"), 160, 1, 400)
        try:
            async with self.plugin._data_lock:
                shrinker = getattr(self.plugin, "_shrink_user_proactive_candidates", None)
                if not callable(shrinker):
                    return self._error("当前插件缺少主动候选收缩能力")
                removed = int(shrinker(user_id, pending_cap=keep, note="page_prune") or 0)
                self.plugin._save_data_sync(sections={"proactive_candidate_pool"})
                data = self._overview_data_snapshot_locked(self.plugin.data)
            proactive_tasks = await self._proactive_task_summary_async(
                data,
                force_refresh=True,
            )
            return self._ok(
                {
                    "message": f"已为用户压缩 {removed} 条未发送候选",
                    "removed": removed,
                    "kept_limit": keep,
                    "proactive_candidates": self._proactive_candidate_summary(data),
                    "proactive_tasks": proactive_tasks,
                }
            )
        except Exception as exc:
            logger.error(f"压缩主动候选失败: {exc}", exc_info=True)
            return self._exception_error(str(exc))

    async def _run_proactive_message_chain_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, str]] = []

        def add_step(name: str, status: str, detail: str = "") -> None:
            steps.append(
                {
                    "name": self._single_line(name, 40),
                    "status": self._single_line(status, 16) or "info",
                    "detail": self._single_line(detail, 180),
                }
            )

        context = getattr(self.plugin, "context", None)
        if context is None:
            add_step("上下文", "error", "AstrBot context 不可用")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "AstrBot context 不可用",
            }

        target_user_id = self._single_line(payload.get("user_id"), 80)
        async with self.plugin._data_lock:
            users = deepcopy(self.plugin.data.get("users") if isinstance(self.plugin.data.get("users"), dict) else {})
        target_user_id, target_user = self._preferred_proactive_test_user(users, target_user_id)
        if not target_user_id or not target_user:
            add_step("目标会话", "error", "没有找到已启用且带私聊会话的私聊对象")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "steps": steps,
                "error": "没有找到可用于测试的私聊对象",
            }
        stored_umo = self._single_line(target_user.get("umo"), 180)
        route_resolver = getattr(self.plugin, "_private_delivery_umo_for_user_id", None)
        try:
            resolved_umo = self._single_line(route_resolver(target_user_id), 180) if callable(route_resolver) else ""
        except Exception as exc:
            logger.warning(
                "主动消息链路测试解析当前投递会话失败: user=%s error=%s",
                self._single_line(target_user_id, 80),
                self._single_line(exc, 160),
            )
            resolved_umo = ""
        umo = resolved_umo or stored_umo
        if not umo:
            add_step("目标会话", "error", "目标用户缺少 umo")
            return {
                "ok": False,
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "steps": steps,
                "error": "目标用户缺少私聊会话",
            }
        route_note = ""
        if stored_umo and resolved_umo and stored_umo != resolved_umo:
            route_note = "（已切换到当前有效投递会话）"
        add_step("目标会话", "ok", f"用户 {target_user.get('nickname') or target_user_id} / {umo}{route_note}")

        now = time.time()
        delay_seconds = self._int(payload.get("delay_seconds"), 60, 5, 300)
        scheduled_ts = now + delay_seconds
        test_id = self._single_line(payload.get("_test_request_id"), 32) or secrets.token_hex(6)
        plan_keys = (
            "next_proactive_at",
            "planned_proactive_reason",
            "planned_proactive_action",
            "planned_proactive_source",
            "planned_proactive_kind",
            "planned_proactive_route_version",
            "planned_proactive_route_dedupe_key",
            "planned_proactive_route_review_profile",
            "planned_proactive_route_retry_profile",
            "planned_proactive_route_cancel_if_new_inbound",
            "planned_proactive_route_recent_chat_policy",
            "planned_proactive_route_allow_automatic_followup",
            "planned_proactive_route_disable_segmenting",
            "planned_proactive_response_expectation",
            "planned_proactive_origin_event_id",
            "planned_proactive_route_preflight_action",
            "planned_proactive_route_preflight_note",
            "planned_proactive_motive",
            "planned_proactive_topic",
            "planned_proactive_impulse_id",
            "planned_proactive_window_start_at",
            "planned_proactive_best_until_at",
            "planned_proactive_expire_at",
            "planned_proactive_origin_at",
            "planned_proactive_origin_key",
            "planned_proactive_freshness",
            "planned_proactive_delivery_state",
            "planned_proactive_semantic_kind",
            "planned_proactive_anchor_type",
            "planned_proactive_semantic_score",
            "planned_proactive_semantic_note",
            "planned_proactive_model_judge_signature",
            "planned_proactive_model_judge_result",
            "planned_proactive_model_judge_at",
            "planned_event_chain",
            "planned_opener_mode",
            "planned_followup_kind",
            "planned_proactive_quota_exempt",
            "planned_proactive_window_timezone",
            "planned_candidate_id",
            "planned_proactive_trigger_message_id",
            "planned_proactive_trigger_umo",
            "planned_proactive_trigger_ts",
            "planned_proactive_trigger_inbound_count",
            "planned_proactive_trigger_created_at",
            "llm_timer_event",
            "sent_today",
            "proactive_sent_count",
            "ignored_streak",
            "awaiting_reply_since",
            "last_sent",
            "last_companion_message",
            "last_proactive_reason",
            "last_proactive_action",
            "last_proactive_behavior_summary",
            "last_proactive_motive",
            "last_proactive_kind",
            "proactive_route_sent_counts",
            "recent_proactive_topics",
            "proactive_daypart_counts",
            "proactive_afterglow",
            "recent_proactive_afterglows",
            "recent_proactive_hesitations",
            "last_proactive_hesitation_at",
            "last_proactive_hesitation_note",
            "state_continuity",
            "pending_followup_event",
            "suspended_proactive",
            "poke_daily_limit",
            "photo_daily_limit",
            "screen_peek_daily_limit",
        )

        async with self.plugin._data_lock:
            current = self.plugin._get_user(target_user_id)
            if not isinstance(current, dict) or not current.get("enabled", True):
                add_step("临时任务", "error", "目标私聊对象未启用")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "目标私聊对象未启用",
                }
            if current.get("proactive_sending"):
                add_step("临时任务", "error", "该用户已有主动发送正在进行")
                return {
                    "ok": False,
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": steps,
                    "error": "该用户已有主动发送正在进行",
                }
            if (
                str(current.get("planned_proactive_source") or "") == "troubleshooting"
                and isinstance(current.get("troubleshooting_proactive_restore"), dict)
            ):
                existing_steps = current.get("troubleshooting_proactive_steps") if isinstance(current.get("troubleshooting_proactive_steps"), list) else []
                return {
                    "ok": True,
                    "pending": True,
                    "trace_id": self._single_line(current.get("troubleshooting_proactive_test_id"), 32),
                    "title": "主动消息链路测试",
                    "user_id": target_user_id,
                    "umo": umo,
                    "steps": existing_steps,
                    "detail": "已有一个排障临时主动任务在等待执行，请稍后刷新查看结果",
                    "action": "message",
                    "reason": self._single_line(current.get("planned_proactive_reason"), 40) or "check_in",
                    "error": "",
                }
            restore = {
                "values": {key: deepcopy(current[key]) for key in plan_keys if key in current},
                "missing": [key for key in plan_keys if key not in current],
            }
            current["troubleshooting_proactive_restore"] = restore
            current["troubleshooting_proactive_test_id"] = test_id
            current["troubleshooting_proactive_started_at"] = now
            current["troubleshooting_proactive_steps"] = [
                {
                    "name": "目标会话",
                    "status": "ok",
                    "detail": f"用户 {current.get('nickname') or target_user_id} / {umo}{route_note}",
                },
                {"name": "临时任务", "status": "ok", "detail": f"已预约 {delay_seconds} 秒后由主动循环执行"},
            ]
            current["user_id"] = str(current.get("user_id") or target_user_id)
            current["umo"] = umo
            self.plugin._reset_planned_proactive_delivery_state(current)
            current["next_proactive_at"] = scheduled_ts
            current["planned_proactive_reason"] = "check_in"
            current["planned_proactive_action"] = "message"
            current["planned_proactive_source"] = "troubleshooting"
            current["planned_proactive_motive"] = "对方希望你主动来找一下,轻轻开口确认主动消息链路能正常工作。"
            current["planned_proactive_topic"] = "主动来找对方一下"
            current["planned_proactive_impulse_id"] = ""
            current["planned_proactive_window_start_at"] = scheduled_ts
            timezone_resolver = getattr(self.plugin, "_proactive_window_timezone", None)
            current["planned_proactive_window_timezone"] = (
                self._single_line(timezone_resolver(), 64)
                if callable(timezone_resolver)
                else self._single_line(
                    getattr(self.plugin, "environment_perception_timezone", ""),
                    64,
                )
            ) or "Asia/Shanghai"
            try:
                active_span, grace_span = self.plugin._proactive_impulse_default_window_seconds(
                    "check_in",
                    source="troubleshooting",
                )
            except TypeError:
                active_span, grace_span = self.plugin._proactive_impulse_default_window_seconds("check_in")
            current["planned_proactive_best_until_at"] = scheduled_ts + active_span
            current["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
            semantics = self.plugin._planned_proactive_semantics(current)
            current["planned_proactive_semantic_kind"] = self._single_line(semantics.get("kind"), 40)
            current["planned_proactive_anchor_type"] = self._single_line(semantics.get("anchor_type"), 40)
            current["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, self._float(semantics.get("score")))) * 100)
            current["planned_proactive_semantic_note"] = self._single_line(semantics.get("note"), 180)
            current["planned_event_chain"] = []
            current["planned_opener_mode"] = ""
            current["planned_followup_kind"] = ""
            current["planned_proactive_quota_exempt"] = True
            current["planned_candidate_id"] = f"troubleshooting_{test_id}"
            route_store = getattr(self.plugin, "_store_planned_proactive_route_fields", None)
            if callable(route_store):
                route_store(
                    current,
                    {
                        "source": "troubleshooting",
                        "reason": "check_in",
                        "scheduled_ts": scheduled_ts,
                        "topic": current["planned_proactive_topic"],
                        "motive": current["planned_proactive_motive"],
                        "origin_event_id": current["planned_candidate_id"],
                    },
                )
            current["llm_timer_event"] = {}
            current["poke_daily_limit"] = 0
            current["photo_daily_limit"] = 0
            current["screen_peek_daily_limit"] = 0
            result = {
                "ok": True,
                "pending": True,
                "trace_id": test_id,
                "outcome_type": "waiting_schedule",
                "title": "主动消息链路测试",
                "user_id": target_user_id,
                "umo": umo,
                "steps": list(current["troubleshooting_proactive_steps"]),
                "detail": f"已预约 {delay_seconds} 秒后的排障临时主动任务；到点后由主动循环走完整生成、复核、发送和归档流程",
                "action": "message",
                "reason": "check_in",
                "error": "",
            }
            raw = self.plugin.data.setdefault("troubleshooting_test_results", {})
            if not isinstance(raw, dict):
                raw = {}
                self.plugin.data["troubleshooting_test_results"] = raw
            raw["proactive_message"] = self._sanitize_troubleshooting_test_result(result)
            self._save_plugin_sections(self.plugin, {"users", "troubleshooting_test_results"})
        wakeup_task = self._schedule_troubleshooting_proactive_wakeup(target_user_id, scheduled_ts)
        if wakeup_task is None:
            logger.info(
                "主动消息链路测试未建立单独唤醒任务，将继续等待常驻主动循环: user=%s",
                self._single_line(target_user_id, 80),
            )
        add_step("临时任务", "ok", f"已预约 {delay_seconds} 秒后由主动循环执行")
        return result

    def _preferred_proactive_test_user(
        self,
        users: dict[str, Any],
        preferred_user_id: str = "",
    ) -> tuple[str, dict[str, Any] | None]:
        if preferred_user_id:
            item = users.get(preferred_user_id)
            if isinstance(item, dict) and item.get("enabled", True) and item.get("umo"):
                item = deepcopy(item)
                item["user_id"] = str(item.get("user_id") or preferred_user_id)
                return preferred_user_id, item
        fallback: tuple[str, dict[str, Any] | None] = ("", None)
        for user_id, item in users.items():
            if not isinstance(item, dict) or not item.get("enabled", True) or not item.get("umo"):
                continue
            candidate = deepcopy(item)
            candidate["user_id"] = str(candidate.get("user_id") or user_id)
            if not fallback[0]:
                fallback = (str(user_id), candidate)
            if self.plugin._private_user_role(candidate, str(candidate.get("user_id") or user_id)) == "owner":
                return str(user_id), candidate
        return fallback

    def _proactive_candidate_block_is_normal(self, note: str) -> bool:
        text = self._single_line(note, 180)
        if not text:
            return True
        exact_normal = {
            "已有更早主动候选",
            "近期主题过于相似",
            "已有用户预约/定时主动",
            "用户明确休息中",
            "当前时段主动已足够,已避开扎堆",
            "朋友主动已按日内节奏延后",
            "已有更早主动候选",
            "用户在该问候窗口内已经活跃过",
            "潜在念头窗口已过期",
            "多来源合并",
            "群聊分享候选已过期",
        }
        if text in exact_normal:
            return True
        normal_tokens = (
            "已有更早",
            "近期主题",
            "过于相似",
            "用户明确休息",
            "休息中",
            "免打扰",
            "已避开扎堆",
            "按日内节奏延后",
            "额度",
            "冷却",
            "间隔",
            "频率",
            "调度过滤",
            "窗口已过期",
            "已经活跃过",
            "多来源合并",
            "候选已过期",
        )
        return any(token in text for token in normal_tokens)
