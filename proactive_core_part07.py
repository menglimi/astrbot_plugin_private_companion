# -*- coding: utf-8 -*-
"""ProactivePart07Mixin。

由 tools/split_mixin_domain.py 从 proactive.py 机械抽取（26 个方法 + 0 个模块级名字 + 0 个类级赋值 / 562 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMixin）。
"""
from __future__ import annotations

from .proactive_core_shared import (
    _ANONYMOUS_AREA_DWELL_THRESHOLDS_SECONDS,
    _ANONYMOUS_AREA_PENDING_TTL_SECONDS,
    _ANONYMOUS_AREA_STABLE_GAP_SECONDS,
    _ANONYMOUS_AREA_VISIT_GAP_SECONDS,
    _MOBILE_LOCATION_HUMANIZATION_BUDGET_SECONDS,
    _proactive_setting_value,
    logger,
)
from .proactive_core_shared import Any
from .proactive_core_shared import _now_ts
from .proactive_core_shared import _safe_float
from .proactive_core_shared import _safe_int
from .proactive_core_shared import _single_line
from .proactive_core_shared import asyncio
from .proactive_core_shared import hashlib
from .proactive_core_shared import inspect



class ProactivePart07Mixin:
    """ProactivePart07Mixin（从 ProactiveMixin 拆出）。"""


    @staticmethod
    def _random_impulse_slot_open(
        active_impulses: list[dict[str, Any]],
        *,
        now: float,
        delay_hours: tuple[float, float] | None,
    ) -> bool:
        """True when nothing queued/deferred starts before the random-draw horizon.

        Ritual greetings (noon/evening) are queued hours ahead; they must not
        stop the engine from drawing an ordinary impulse for the gap before them.
        """
        high = _safe_float(delay_hours[1], 1.0, 0.05) if delay_hours else 1.0
        horizon = now + max(0.5, high) * 3600
        return not any(
            _safe_float(item.get("window_start_at"), 0) <= horizon
            for item in active_impulses
            if isinstance(item, dict)
        )

    def _promote_earlier_daily_greeting_event(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        if str(user.get("planned_proactive_source") or "") == "timer":
            return False
        current_next = _safe_float(user.get("next_proactive_at"), 0)
        if current_next <= 0:
            return False
        now = now or _now_ts()
        events = []
        if bool(_proactive_setting_value(self, "enable_daily_greetings", True)):
            events.append(self._pick_daily_greeting_event(user, now))
        if bool(_proactive_setting_value(self, "enable_meal_care_proactive", True)):
            events.append(self._pick_meal_care_event(user, now=now))
        events.extend(
            (
                self._pick_birthday_celebration_event(user, now),
                self._pick_special_day_greeting_event(user, now=now),
                self._pick_insomnia_night_event(user, now=now),
            )
        )
        valid_events = [item for item in events if isinstance(item, dict)]
        if not valid_events:
            return False
        event = min(valid_events, key=lambda item: self._timestamp_from_story_event(item, str(item.get("reason") or "check_in")))
        reason = str(event.get("reason") or "")
        priority_reasons = {"birthday_celebration", "special_day_greeting", "insomnia_night"}
        if not (
            self._is_sticky_greeting_reason(reason)
            or bool(event.get("_daily_meal_care"))
            or reason in priority_reasons
        ):
            return False
        source = _single_line(event.get("_proactive_source"), 40)
        if not source:
            source = "daily_greeting" if event.get("_daily_greeting") else "meal_care"
        prepared, _invalid_reason = self._prepare_proactive_candidate_window(
            event,
            reason=reason,
            source=source,
            now=now,
        )
        if not isinstance(prepared, dict):
            return False
        event = prepared
        scheduled = _safe_float(
            event.get("scheduled_ts"),
            self._timestamp_from_story_event(event, reason),
        )
        if scheduled <= 0 or scheduled >= current_next - 60:
            return False
        action = str(event.get("action") or "message")
        motive = _single_line(event.get("motive"), 120) or self._choose_proactive_motive(
            reason,
            user,
            action=action,
            planned_event=event,
        )
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = scheduled
        user["planned_proactive_reason"] = reason
        user["planned_proactive_action"] = action
        user["planned_proactive_source"] = source
        user["planned_proactive_conversation_posture"] = _single_line(event.get("conversation_posture"), 24).lower()
        user["planned_proactive_conversation_closing_deferred"] = False
        user["planned_proactive_motive"] = motive
        user["planned_proactive_topic"] = _single_line(event.get("topic"), 60)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = _safe_float(event.get("window_start_at"), scheduled)
        user["planned_proactive_window_timezone"] = _single_line(
            event.get("window_timezone"),
            64,
        ) or self._proactive_window_timezone()
        user["planned_proactive_best_until_at"] = _safe_float(event.get("best_until_at"), scheduled)
        user["planned_proactive_expire_at"] = _safe_float(event.get("expire_at"), scheduled)
        # 该入口会替换当前计划，但不消费原念头；不能让新问候继续引用旧候选 ID。
        user["planned_candidate_id"] = ""
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        self._clear_planned_proactive_trigger(user)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(event.get("chain") or []) if isinstance(event.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = bool(event.get("_free_screen_peek"))
        self._store_planned_proactive_route_fields(user, {**event, "source": source})
        context_key = _single_line(event.get("context_key"), 60)
        context = event.get("context")
        if context_key and isinstance(context, dict):
            user[context_key] = dict(context)
        return True

    def _is_proactive_plan_stale(self, user: dict[str, Any], *, now: float | None = None) -> bool:
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        if next_at <= 0:
            return False
        check_now = _now_ts() if now is None else now
        return check_now - next_at > _safe_int(
            _proactive_setting_value(self, "max_proactive_plan_lag_minutes", 180),
            180,
            5,
            1440,
        ) * 60

    def _reset_planned_proactive_delivery_state(self, user: dict[str, Any]) -> None:
        user["planned_proactive_origin_at"] = 0
        user["planned_proactive_origin_key"] = ""
        user["planned_proactive_freshness"] = ""
        user["planned_proactive_delivery_state"] = ""

    def _clear_pending_proactive_plan(self, user: dict[str, Any]) -> None:
        current_impulse_id = _single_line(user.get("planned_proactive_impulse_id"), 20)
        user.pop("body_monitor_health_context", None)
        impulses = user.get("proactive_impulses")
        if current_impulse_id and isinstance(impulses, list):
            for impulse in impulses:
                if not isinstance(impulse, dict) or _single_line(impulse.get("id"), 20) != current_impulse_id:
                    continue
                if _single_line(impulse.get("source"), 40) == "body_monitor":
                    impulse.pop("context", None)
                    impulse["context_key"] = ""
                break
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_conversation_posture"] = ""
        user["planned_proactive_conversation_closing_deferred"] = False
        user["planned_proactive_kind"] = ""
        user["planned_proactive_route_version"] = 0
        user["planned_proactive_route_dedupe_key"] = ""
        user["planned_proactive_route_review_profile"] = ""
        user["planned_proactive_route_retry_profile"] = ""
        user["planned_proactive_route_cancel_if_new_inbound"] = True
        user["planned_proactive_route_recent_chat_policy"] = ""
        user["planned_proactive_route_allow_automatic_followup"] = False
        user["planned_proactive_route_disable_segmenting"] = False
        user["planned_proactive_response_expectation"] = ""
        user["planned_proactive_burst"] = False
        user["proactive_burst_index"] = 0
        user["proactive_burst_origin_id"] = ""
        user["planned_proactive_origin_event_id"] = ""
        user["planned_proactive_route_preflight_action"] = ""
        user["planned_proactive_route_preflight_note"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_proactive_impulse_id"] = ""
        user["planned_mobile_location_transition_key"] = ""
        user["planned_mobile_location_event_type"] = ""
        user["planned_proactive_window_start_at"] = 0
        user["planned_proactive_window_timezone"] = ""
        user["planned_proactive_best_until_at"] = 0
        user["planned_proactive_expire_at"] = 0
        self._reset_planned_proactive_delivery_state(user)
        user["planned_proactive_semantic_kind"] = ""
        user["planned_proactive_anchor_type"] = ""
        user["planned_proactive_semantic_score"] = 0
        user["planned_proactive_semantic_note"] = ""
        user["planned_proactive_model_judge_signature"] = ""
        user["planned_proactive_model_judge_result"] = {}
        user["planned_proactive_model_judge_at"] = 0
        user["planned_event_chain"] = []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False
        user["planned_candidate_id"] = ""
        self._clear_planned_proactive_trigger(user)

    def _maintenance_failure_cooldown_seconds(self, label: str) -> float:
        if label in {"日常状态", "今日日程", "当前细化", "日记", "每日巡视", "创作推进"}:
            return 30 * 60
        return 5 * 60

    def _maintenance_task_blocked_by_failure(self, label: str, *, now: float | None = None) -> str:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            return ""
        key = self._maintenance_failure_key(label)
        item = state.get(key)
        if not isinstance(item, dict):
            return ""
        check_now = _now_ts() if now is None else now
        until = _safe_float(item.get("until"), 0, 0)
        if until <= check_now:
            state.pop(key, None)
            return ""
        error = _single_line(item.get("error"), 120)
        return f"{label} 失败冷却中（{self._format_elapsed(until - check_now)}后重试" + (f"，上次错误：{error}" if error else "") + "）"

    def _record_maintenance_task_failure(self, label: str, exc: Exception) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if not isinstance(state, dict):
            state = {}
            self._maintenance_failure_cooldowns = state
        now = _now_ts()
        state[self._maintenance_failure_key(label)] = {
            "until": now + self._maintenance_failure_cooldown_seconds(label),
            "error": _single_line(exc, 180),
            "failed_at": now,
        }

    def _clear_maintenance_task_failure(self, label: str) -> None:
        state = getattr(self, "_maintenance_failure_cooldowns", None)
        if isinstance(state, dict):
            state.pop(self._maintenance_failure_key(label), None)

    def _maintenance_failure_key(self, label: str) -> str:
        active_getter = getattr(self, "_active_persona_scope", None)
        persona_id = str(active_getter() if callable(active_getter) else "").strip()
        return f"{persona_id}:{label}" if persona_id else label

    def _maintenance_task_min_interval_seconds(self, label: str) -> float:
        """Per-task minimum cadence for the scheduler loop so long-running
        maintenance work does not need to be re-invoked every cycle."""
        intervals = {
            "被动注入缓存": 300.0,
            "日程归档": 600.0,
            "日记": 300.0,
            "每日巡视": 300.0,
            "每日穿搭": 300.0,
            "个人目标": 300.0,
            "今日日程": 120.0,
            "当前细化": 120.0,
            "当前在线感": 120.0,
            "天气预警": 120.0,
            "环境突变": 120.0,
            "余额感知": 120.0,
            "日常状态": 120.0,
            "创作推进": 60.0,
            "备忘便签": 60.0,
            "晚安识屏": 60.0,
        }
        return _safe_float(intervals.get(label), 60.0, 30.0, 3600.0)

    def _maintenance_task_due(self, label: str, *, now: float | None = None) -> bool:
        last_run = getattr(self, "_maintenance_task_last_run", None)
        if not isinstance(last_run, dict):
            return True
        check_now = _now_ts() if now is None else now
        stamp = _safe_float(last_run.get(self._maintenance_failure_key(label)), 0.0)
        if stamp <= 0:
            return True
        return check_now - stamp >= self._maintenance_task_min_interval_seconds(label)

    def _touch_maintenance_task_run(self, label: str) -> None:
        last_run = getattr(self, "_maintenance_task_last_run", None)
        if not isinstance(last_run, dict):
            last_run = {}
            self._maintenance_task_last_run = last_run
        last_run[self._maintenance_failure_key(label)] = _now_ts()

    def _scheduler_maintenance_tasks_due(self) -> tuple[tuple[str, Any], ...]:
        return tuple(
            item
            for item in self._scheduler_maintenance_tasks()
            if self._maintenance_task_due(item[0])
        )

    def _scheduler_maintenance_tasks(self) -> tuple[tuple[str, Any], ...]:
        tasks = (
            ("日常状态", self._ensure_daily_state),
            ("今日日程", self._ensure_daily_plan),
            ("日程归档", self._run_agenda_maintenance_tick),
            ("当前细化", self._ensure_detail_enhancement),
            ("当前在线感", self._ensure_current_detail_presence_status),
            ("日记", self._ensure_daily_diary),
            ("每日巡视", self._ensure_daily_review),
            ("每日穿搭", self._ensure_daily_outfit_photo),
            ("创作推进", self._maybe_advance_creative_projects),
            ("个人目标", self._maybe_settle_personal_goals),
            ("备忘便签", self._maybe_process_memo_notes),
            ("天气预警", self._maybe_refresh_weather_alerts),
            ("环境突变", self._maybe_refresh_environment_change),
            ("余额感知", self._maybe_refresh_balance_awareness),
            ("晚安识屏", self._maybe_process_goodnight_screen_checks),
            ("被动注入缓存", self._refresh_passive_injection_cache),
        )
        if not self._proactive_generation_disabled():
            return tasks
        passive_labels = {
            "日常状态",
            "今日日程",
            "日程归档",
            "当前细化",
            "当前在线感",
            "日记",
            "每日巡视",
            "天气预警",
            "晚安识屏",
            "被动注入缓存",
        }
        return tuple(item for item in tasks if item[0] in passive_labels)

    async def _run_agenda_maintenance_tick(self) -> list[dict[str, Any]]:
        """Settle local windows, archive compact projections, then drain outbox."""
        tick = getattr(self, "_agenda_maintenance_tick", None)
        settled: Any = []
        if callable(tick):
            settled = tick()
            if inspect.isawaitable(settled):
                settled = await settled
        snapshots = [item for item in settled if isinstance(item, dict)] if isinstance(settled, list) else []
        snapshot_recorder = getattr(self, "_memory_companion_record_agenda_snapshot", None)
        reconciliation_recorder = getattr(self, "_memory_companion_record_agenda_reconciliation", None)
        history = self.data.get("agenda_reconciliation_history") if isinstance(getattr(self, "data", None), dict) else []
        for snapshot in snapshots:
            if callable(snapshot_recorder):
                try:
                    await snapshot_recorder(snapshot)
                except Exception as exc:
                    logger.debug("C3 agenda snapshot archival failed: %s", _single_line(exc, 160))
            if callable(reconciliation_recorder) and isinstance(history, list):
                snapshot_id = _single_line(snapshot.get("snapshot_id"), 160)
                for reconciliation in reversed(history):
                    if not isinstance(reconciliation, dict):
                        continue
                    refs = reconciliation.get("source_refs") if isinstance(reconciliation.get("source_refs"), list) else []
                    if snapshot_id and snapshot_id not in refs:
                        continue
                    try:
                        await reconciliation_recorder(reconciliation)
                    except Exception as exc:
                        logger.debug("C3 agenda reconciliation archival failed: %s", _single_line(exc, 160))
                    break
        flusher = getattr(self, "_memory_companion_flush_bot_personal_outbox", None)
        if callable(flusher):
            try:
                await flusher(limit=24)
            except Exception as exc:
                logger.debug("C3 Bot Personal outbox delivery failed: %s", _single_line(exc, 160))
        if snapshots and callable(getattr(self, "_schedule_data_save", None)):
            self._schedule_data_save(
                sections={"window_snapshots", "agenda_reconciliation_history"},
                delay=0.5,
            )
        return snapshots

    def _scheduler_persona_ids(self) -> list[str]:
        active_getter = getattr(self, "_active_persona_scope", None)
        active = str(active_getter() if callable(active_getter) else "").strip()
        if not bool(getattr(self, "enable_multi_persona_mode", False)):
            return [""]
        primary_getter = getattr(self, "_primary_persona_id", None)
        try:
            primary = str(primary_getter() or "").strip() if callable(primary_getter) else ""
        except Exception:
            primary = ""
        primary = primary or str(getattr(self, "plugin_specific_persona_id", "") or "").strip()
        configured_getter = getattr(self, "_persona_config_profile_ids", None)
        configured_profiles = list(configured_getter() if callable(configured_getter) else [])
        enabled_getter = getattr(self, "_configured_multi_persona_ids", None)
        enabled_ids = set(enabled_getter() if callable(enabled_getter) else [])
        ids = [primary, *(pid for pid in configured_profiles if pid in enabled_ids)]
        enabled = list(dict.fromkeys(item for item in ids if item))
        if active and active in enabled:
            return [active]
        return enabled or [""]

    async def _run_scheduler_cycle(self, *, immediate: bool = False) -> None:
        active_getter = getattr(self, "_active_persona_scope", None)
        current = str(active_getter() if callable(active_getter) else "").strip()
        for persona_id in self._scheduler_persona_ids():
            token = None
            if persona_id and persona_id != current:
                activator = getattr(self, "_activate_persona_id", None)
                token = activator(persona_id) if callable(activator) else None
            try:
                await self._tick()
                maintenance_tasks = (
                    self._scheduler_maintenance_tasks()
                    if immediate
                    else self._scheduler_maintenance_tasks_due()
                )
                for label, task_factory in maintenance_tasks:
                    try:
                        if self._maintenance_task_blocked_by_failure(label):
                            continue
                        await task_factory()
                        self._clear_maintenance_task_failure(label)
                        self._touch_maintenance_task_run(label)
                    except Exception as exc:
                        self._record_maintenance_task_failure(label, exc)
                        logger.warning(
                            "%s维护步骤失败,已跳过: persona=%s task=%s error=%s",
                            "主动链即时" if immediate else "主动循环",
                            persona_id or "single",
                            label,
                            _single_line(exc, 160),
                        )
            finally:
                if token is not None:
                    deactivator = getattr(self, "_deactivate_persona_for_event", None)
                    if callable(deactivator):
                        deactivator(token)

    async def _scheduler_loop(self):
        while not self._stop_event.is_set():
            try:
                timeout = self._next_scheduler_timeout()
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=timeout
                )
            except asyncio.TimeoutError:
                await self._run_scheduler_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"主动消息循环异常: {e}", exc_info=True)

    def _mobile_location_watch_user_ids(self) -> list[str]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        owner_getter = getattr(self, "_relationship_owner_user_ids", None)
        target_getter = getattr(self, "_configured_target_ids", None)
        allowed = {
            _single_line(item, 120)
            for getter in (owner_getter, target_getter)
            if callable(getter)
            for item in (getter() or ())
            if _single_line(item, 120)
        }
        if not allowed:
            allowed = {
                _single_line(key, 120)
                for key, value in users.items()
                if isinstance(value, dict) and value.get("reality_touch_consent")
            }
        return [
            user_id
            for user_id, user in users.items()
            if _single_line(user_id, 120) in allowed
            and isinstance(user, dict)
            and bool(user.get("enabled", True))
        ]

    @staticmethod
    def _anonymous_area_token(scene: dict[str, Any]) -> str:
        """Return an opaque kilometre-scale token; never persist raw location data."""
        if not isinstance(scene, dict):
            return ""
        area = _single_line(scene.get("area_label"), 100)
        if not area:
            return ""
        # Keep the durable token at city/district granularity. The raw area
        # label is never stored, and no coordinate is needed for this social
        # cue; a broader token also avoids pretending to recognise a venue.
        return hashlib.sha256(area.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _anonymous_area_is_stable(scene: dict[str, Any]) -> bool:
        if not isinstance(scene, dict) or not scene.get("available"):
            return False
        if scene.get("presence_state") in {"at_place", "departing", "arriving", "in_transit"}:
            return False
        return not bool(scene.get("in_motion"))

    def _anonymous_area_runtime_store(self) -> dict[str, dict[str, Any]]:
        store = getattr(self, "_mobile_anonymous_area_runtime", None)
        if not isinstance(store, dict):
            store = {}
            self._mobile_anonymous_area_runtime = store
        return store

    def _anonymous_area_visit_records(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        records = user.get("mobile_anonymous_area_visits")
        if not isinstance(records, list):
            records = []
            user["mobile_anonymous_area_visits"] = records
        return [item for item in records if isinstance(item, dict)]

    @staticmethod
    def _mobile_location_humanization_budget_available(
        user: dict[str, Any],
        *,
        now: float,
    ) -> bool:
        """Keep location-derived social cues from piling up in one hour."""
        last_at = _safe_float(user.get("last_mobile_location_humanization_at"), 0.0)
        return last_at <= 0 or now - last_at >= _MOBILE_LOCATION_HUMANIZATION_BUDGET_SECONDS

    def _observe_mobile_anonymous_area(
        self,
        user: dict[str, Any],
        scene: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        """Track coarse unmarked-area dwell without writing every GPS sample."""
        if not isinstance(user, dict):
            return False
        check_now = _now_ts() if now is None else float(now)
        user_id = _single_line(user.get("user_id") or user.get("id"), 120)
        if not user_id:
            return False
        store = self._anonymous_area_runtime_store()
        current = store.get(user_id) if isinstance(store.get(user_id), dict) else {}
        token = self._anonymous_area_token(scene) if self._anonymous_area_is_stable(scene) else ""
        previous_token = _single_line(current.get("token"), 40)
        last_seen = _safe_float(current.get("last_seen_at"), 0.0)
        changed = False

        def finish_previous(dwell_end: float) -> None:
            nonlocal changed
            if not previous_token:
                return
            dwell_seconds = max(0.0, dwell_end - _safe_float(current.get("started_at"), dwell_end))
            policy_getter = getattr(self, "_proactive_quota_policy", None)
            policy = policy_getter(user) if callable(policy_getter) else {}
            tier = _safe_int(policy.get("tier"), 3, 1, 5) if isinstance(policy, dict) else 3
            threshold = _ANONYMOUS_AREA_DWELL_THRESHOLDS_SECONDS.get(tier, 10**9)
            visits = self._anonymous_area_visit_records(user)
            visit = next((item for item in visits if _single_line(item.get("token"), 40) == previous_token), None)
            familiar = _safe_int(visit.get("count"), 0, 0) >= 3 if isinstance(visit, dict) else False
            if dwell_seconds >= threshold or familiar:
                user["mobile_anonymous_area_pending"] = {
                    "token": previous_token,
                    "left_at": check_now,
                    "dwell_minutes": int(round(dwell_seconds / 60.0)),
                    "visit_count": _safe_int(visit.get("count"), 1, 1) if isinstance(visit, dict) else 1,
                    "familiar": familiar,
                    "expires_at": check_now + _ANONYMOUS_AREA_PENDING_TTL_SECONDS,
                }
                changed = True
            store.pop(user_id, None)

        if not token:
            # 客户端只在到达/离开时上传定位，停留期内没有新的观察刷新
            # last_seen；离开确认本身就是停留的终点，用本次检查时间补全
            # 整段 dwell，不再依赖中途轮询。
            finish_previous(check_now)
            return changed
        if previous_token and previous_token != token:
            # 换区即离开已确认，停留终点是本次检查时间。
            finish_previous(check_now)
            current = {}
            previous_token = ""
        if previous_token and last_seen > 0 and check_now - last_seen > _ANONYMOUS_AREA_STABLE_GAP_SECONDS:
            # 观察中断太久，中断期间的停留不可信，保守用最后可见时间结算，
            # 不虚增 dwell 也不把还在原地的用户误判成“离开后”。
            finish_previous(last_seen)
            current = {}
            previous_token = ""
        if not previous_token:
            started_at = check_now
            current = {"token": token, "started_at": started_at, "last_seen_at": check_now}
            store[user_id] = current
            visits = self._anonymous_area_visit_records(user)
            visit = next((item for item in visits if _single_line(item.get("token"), 40) == token), None)
            if not isinstance(visit, dict) or check_now - _safe_float(visit.get("last_visit_at"), 0.0) > _ANONYMOUS_AREA_VISIT_GAP_SECONDS:
                if not isinstance(visit, dict):
                    visit = {"token": token, "count": 0, "first_visit_at": check_now}
                    visits.append(visit)
                visit["count"] = min(20, _safe_int(visit.get("count"), 0, 0) + 1)
                visit["last_visit_at"] = check_now
                user["mobile_anonymous_area_visits"] = visits[-8:]
                changed = True
            return changed
        current["last_seen_at"] = check_now
        return changed
