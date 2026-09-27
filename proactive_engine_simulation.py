# -*- coding: utf-8 -*-
"""模拟/调试回放域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 455 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import random
from .constants import _SIMULATION_FALLBACK_EVENTS
from .conversation_prompt_section import PromptRenderMode, prompt_section, render_prompt_sections
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .proactive_engine_shared import _engine_proactive_window_timezone, _persona_provider_id
from typing import Any



class ProactiveEngineSimulationMixin:
    """模拟/调试回放域（从 ProactiveEngineMixin 拆出）。"""


    def _simulation_label(self, user: dict[str, Any]) -> str:
        raw = user.get("simulation_mode")
        if isinstance(raw, dict):
            label = _single_line(raw.get("label"), 24)
            if label:
                return label
        return "压缩测试"

    def _should_send_simulation(self, user: dict[str, Any]) -> tuple[bool, str]:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict) or not sim.get("active"):
            return False, "未处于模拟模式"
        now = _engine_host._now_ts()
        self._sync_simulation_next_event(user, now=now)
        next_at = _safe_float(user.get("next_proactive_at"), 0)
        label = self._simulation_label(user)
        if next_at <= 0:
            self._finish_simulation_mode(user)
            return False, f"{label}已结束"
        if now < next_at:
            return False, f"{label}等待下一条主动消息"
        return True, "simulation"

    def _sync_simulation_next_event(self, user: dict[str, Any], *, now: float | None = None) -> None:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict):
            return
        events = sim.get("events")
        if not isinstance(events, list) or not events:
            self._finish_simulation_mode(user)
            return
        now = now or _engine_host._now_ts()
        remaining = [event for event in events if isinstance(event, dict)]
        if not remaining:
            self._finish_simulation_mode(user)
            return
        remaining.sort(key=lambda item: _safe_float(item.get("_scheduled_ts"), now))
        sim["events"] = remaining
        current = remaining[0]
        user["next_proactive_at"] = _safe_float(current.get("_scheduled_ts"), now)
        user["planned_proactive_reason"] = self._normalize_legacy_proactive_text(current.get("reason"), limit=40) or "check_in"
        user["planned_proactive_action"] = self._normalize_legacy_proactive_text(current.get("action"), limit=40) or "message"
        user["planned_proactive_source"] = "simulation"
        user["planned_proactive_conversation_posture"] = _single_line(current.get("conversation_posture"), 24).lower()
        user["planned_proactive_conversation_closing_deferred"] = False
        user["planned_proactive_motive"] = _single_line(current.get("motive"), 140)
        user["planned_proactive_topic"] = _single_line(current.get("topic"), 60)
        scheduled_ts = _safe_float(user.get("next_proactive_at"), now)
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = scheduled_ts
        user["planned_proactive_window_timezone"] = _engine_proactive_window_timezone(self)
        active_span, grace_span = self._proactive_impulse_default_window_seconds(
            user["planned_proactive_reason"],
            source="simulation",
        )
        user["planned_proactive_best_until_at"] = scheduled_ts + active_span
        user["planned_proactive_expire_at"] = scheduled_ts + active_span + grace_span
        semantics = self._planned_proactive_semantics(user)
        user["planned_proactive_semantic_kind"] = _single_line(semantics.get("kind"), 40)
        user["planned_proactive_anchor_type"] = _single_line(semantics.get("anchor_type"), 40)
        user["planned_proactive_semantic_score"] = int(max(0.0, min(1.0, _safe_float(semantics.get("score"), 0.5))) * 100)
        user["planned_proactive_semantic_note"] = _single_line(semantics.get("note"), 180)
        user["planned_event_chain"] = [] if self._private_user_role(user) == "friend" else (
            list(current.get("chain") or []) if isinstance(current.get("chain"), list) else []
        )
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = bool(current.get("_free_screen_peek"))
        self._store_planned_proactive_route_fields(user, {**current, "source": "simulation"})

    def _consume_simulation_event(self, user: dict[str, Any]) -> None:
        sim = user.get("simulation_mode")
        if not isinstance(sim, dict):
            return
        events = sim.get("events")
        if not isinstance(events, list) or not events:
            self._finish_simulation_mode(user)
            return
        sim["events"] = [event for event in events[1:] if isinstance(event, dict)]
        sim["sent_count"] = _safe_int(sim.get("sent_count"), 0, 0) + 1
        self._reset_planned_proactive_delivery_state(user)
        self._sync_simulation_next_event(user)

    def _finish_simulation_mode(self, user: dict[str, Any]) -> None:
        user["simulation_mode"] = {}
        self._reset_planned_proactive_delivery_state(user)
        user["next_proactive_at"] = 0
        user["planned_proactive_reason"] = ""
        user["planned_proactive_action"] = ""
        user["planned_proactive_source"] = ""
        user["planned_proactive_conversation_posture"] = ""
        user["planned_proactive_conversation_closing_deferred"] = False
        user["planned_proactive_kind"] = ""
        user["planned_proactive_motive"] = ""
        user["planned_proactive_topic"] = ""
        user["planned_proactive_impulse_id"] = ""
        user["planned_proactive_window_start_at"] = 0
        user["planned_proactive_window_timezone"] = ""
        user["planned_proactive_best_until_at"] = 0
        user["planned_proactive_expire_at"] = 0
        user["planned_proactive_semantic_kind"] = ""
        user["planned_proactive_anchor_type"] = ""
        user["planned_proactive_semantic_score"] = 0
        user["planned_proactive_semantic_note"] = ""
        user["planned_proactive_need_layer"] = ""
        user["planned_proactive_need_drive"] = ""
        user["planned_proactive_need_note"] = ""
        user["planned_event_chain"] = []
        user["planned_opener_mode"] = ""
        user["planned_followup_kind"] = ""
        user["planned_proactive_quota_exempt"] = False

    def _available_test_actions(self, user: dict[str, Any]) -> list[str]:
        actions = ["message"]
        if self._screen_glance_available(user):
            actions.append("screen_peek")
        if self._photo_text_available(user):
            actions.append("photo_text")
        if self._voice_available(user):
            actions.append("voice")
        actions.extend(f"external:{item['name']}" for item in self._available_external_proactive_abilities(user) if item.get("name"))
        return actions

    def _summarize_test_action_labels(self, actions: list[str]) -> str:
        labels = {
            "message": "文字",
            "screen_peek": "窥屏",
            "photo_text": "发图",
            "poke": "戳一戳",
            "voice": "语音",
        }
        return "、".join(labels.get(action, action) for action in actions)

    def _build_full_test_detail_prompt(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        actions: list[str],
        *,
        missing_actions: list[str] | None = None,
    ) -> str:
        base_prompt = self._build_detail_enhancement_prompt(segment, plan, state)
        action_text = "、".join(actions) if actions else "message"
        test_section = prompt_section(
            key="proactive.full_test",
            title="这次是临时完整主动链测试",
            source="proactive_engine",
            content="\n".join((
            "请只围绕这一段日程生成一串用于真实测试的 proactive_events。",
            "要求它们仍然像正常生活里会长出来的主动消息，不要写成“这是测试”或功能演示。",
            f"这轮测试可用的主动行为有：{action_text}。",
            "请尽量让 proactive_events 覆盖每一种可用行为至少一次；如果某种行为实在不合时宜，也要优先找一个勉强自然的切入点，而不是完全放弃。",
            "这些 proactive_events 之后会被压缩成每两分钟一条真实发送，所以你只需要负责把这一整段里的多个主动契机安排出来。",
            "today_events 仍然保持正常生活感，proactive_events 要像从这段生活里自己长出来。",
            "不要把‘测试’、‘跑满能力’、‘验证功能’写进输出里。",
            )),
        )
        extra_sections = [test_section]
        if missing_actions:
            extra_sections.append(
                prompt_section(
                    key="proactive.full_test.correction",
                    title="补正要求",
                    source="proactive_engine",
                    content="\n".join((
                    f"上一轮结果还缺少这些主动行为：{'、'.join(missing_actions)}。",
                    "这一轮请重点补齐缺失行为，同时保持整段仍然像同一个人的连续生活。",
                    )),
                )
            )
        return (
            base_prompt.rstrip()
            + "\n\n"
            + render_prompt_sections(extra_sections, mode=PromptRenderMode.LABELED_BLOCK)
        )

    async def _generate_full_test_detail_enhancement(
        self,
        segment: dict[str, Any],
        plan: dict[str, Any],
        state: dict[str, Any],
        actions: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        required_actions = [action for action in actions if action in {"message", "screen_peek", "photo_text", "voice"}]
        last_normalized = {
            "summary": "这一段按原日程慢慢推进。",
            "today_events": [],
            "proactive_events": [],
            "long_term_events": [],
        }
        missing_actions = list(required_actions)
        for _ in range(3):
            prompt = self._build_full_test_detail_prompt(
                segment,
                plan,
                state,
                required_actions,
                missing_actions=missing_actions if missing_actions and missing_actions != required_actions else None,
            )
            raw_text = await self._llm_call(
                prompt,
                max_tokens=1000,
                provider_id=self._task_provider(
                    _persona_provider_id(
                        self,
                        "DETAIL_ENHANCEMENT_PROVIDER_ID",
                        "detail_enhancement_provider_id",
                        "complex",
                    ),
                    _persona_provider_id(
                        self, "DAILY_PLAN_PROVIDER_ID", "daily_plan_provider_id", "complex"
                    ),
                    _persona_provider_id(
                        self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"
                    ),
                ),
                task="full_test_detail",
            )
            payload = self._extract_json_payload(raw_text or "")
            if not isinstance(payload, dict):
                continue
            normalized = self._normalize_story_plan(
                {
                    "today_events": payload.get("today_events", []),
                    "proactive_events": payload.get("proactive_events", []),
                    "long_term_events": [],
                }
            )
            normalized["summary"] = _single_line(payload.get("summary"), 160)
            last_normalized = normalized
            present = {
                str(item.get("action") or "message")
                for item in normalized.get("proactive_events", [])
                if isinstance(item, dict)
            }
            missing_actions = [action for action in required_actions if action not in present]
            if not missing_actions:
                break
        return last_normalized, missing_actions

    def _build_full_test_events(
        self,
        detail: dict[str, Any],
        *,
        actions: list[str],
        segment: dict[str, Any] | None = None,
        spacing_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        proactive_events = detail.get("proactive_events", []) if isinstance(detail, dict) else []
        if not isinstance(proactive_events, list):
            proactive_events = []
        usable = [dict(item) for item in proactive_events if isinstance(item, dict)]
        segment_start = _safe_int((segment or {}).get("start"), -1, -1)
        segment_end = _safe_int((segment or {}).get("end"), -1, -1)
        if segment_start >= 0 and segment_end > segment_start:
            scoped: list[dict[str, Any]] = []
            for item in usable:
                start, end = self._parse_window_minutes(str(item.get("window") or ""))
                if start is None or end is None:
                    continue
                if start < segment_start or end > segment_end:
                    continue
                scoped.append(item)
            usable = scoped
        required_actions = [action for action in actions if action in {"message", "screen_peek", "photo_text", "voice"}]
        filtered: list[dict[str, Any]] = []
        for action in required_actions:
            matched = next((item for item in usable if str(item.get("action") or "message") == action and item not in filtered), None)
            if matched:
                filtered.append(matched)
        for item in usable:
            action = str(item.get("action") or "message")
            if action in required_actions and item not in filtered:
                filtered.append(item)
        if not filtered:
            filtered = [dict(item) for item in _SIMULATION_FALLBACK_EVENTS]
            for item in filtered:
                item["motive"] = self._normalize_event_motive(item)
        filtered.sort(
            key=lambda item: (
                (self._parse_window_minutes(str(item.get("window") or ""))[0])
                if self._parse_window_minutes(str(item.get("window") or ""))[0] is not None
                else 24 * 60
            )
        )
        start_ts = _engine_host._now_ts() + 20
        events: list[dict[str, Any]] = []
        for index, item in enumerate(filtered):
            cloned = dict(item)
            cloned["_scheduled_ts"] = start_ts + index * spacing_seconds
            cloned["_simulated_window"] = str(item.get("window") or "")
            events.append(cloned)
        return events

    def _build_single_poke_test_event(
        self,
        *,
        user: dict[str, Any],
        segment: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = (segment or {}).get("item") if isinstance(segment, dict) else {}
        item = item if isinstance(item, dict) else {}
        topic = (
            _single_line(((detail or {}).get("proactive_events") or [{}])[0].get("topic"), 60)
            if isinstance((detail or {}).get("proactive_events"), list) and (detail or {}).get("proactive_events")
            else ""
        )
        if not topic:
            topic = _single_line(item.get("activity"), 60) or "刚才那条内容"
        motive = self._normalize_internal_motive_text(
            f"关于“{topic}”，想用戳一戳提醒一下用户"
        )
        window = ""
        if isinstance(segment, dict):
            start = _safe_int(segment.get("start"), -1, -1)
            end = _safe_int(segment.get("end"), -1, -1)
            if start >= 0 and end > start:
                window = f"{self._minutes_to_hhmm(start)}-{self._minutes_to_hhmm(end)}"
        return {
            "window": window,
            "reason": "diary_share",
            "action": "poke",
            "why": _single_line(item.get("activity"), 80) or "突然很想戳你一下",
            "topic": topic,
            "motive": motive,
            "scene": _single_line(((detail or {}).get("summary")), 80) or "眼前这一小段",
            "tone": "轻轻使坏",
            "impulse": "先戳一下，再看看你会不会回头",
            "chain": [],
            "_scheduled_ts": _engine_host._now_ts() + 3,
            "_simulated_window": window or "立即触发",
        }

    def _build_simulation_greeting_events(self) -> list[dict[str, Any]]:
        return [
            {
                "window": "08:15-10:10",
                "reason": "morning_greeting",
                "action": "message",
                "why": "早上醒来后想打个招呼",
                "topic": "刚醒",
                "scene": "一天刚醒来的时候",
                "tone": "还没完全醒",
                "impulse": "想第一时间说声早",
            },
            {
                "window": "12:05-13:35",
                "reason": "noon_greeting",
                "action": "message",
                "why": "午休或午饭时想起这边",
                "topic": "午饭后那会儿",
                "scene": "午后犯困的时候",
                "tone": "懒洋洋",
                "impulse": "想趁午后休息时打个招呼",
            },
            {
                "window": "20:10-21:20",
                "reason": "evening_greeting",
                "action": "message",
                "why": "晚上节奏慢下来时",
                "topic": "天暗下来那会儿",
                "scene": "晚上安静下来时",
                "tone": "安静",
                "impulse": "想趁还没太晚打个招呼",
                "conversation_posture": "closing",
            },
        ]

    def _build_simulation_events(self, user: dict[str, Any], *, duration_minutes: int = 60) -> list[dict[str, Any]]:
        plan = self.data.get("daily_story_plan", {})
        story_events = plan.get("proactive_events", []) if isinstance(plan, dict) else []
        candidates: list[dict[str, Any]] = []
        if isinstance(story_events, list):
            candidates.extend(event for event in story_events if isinstance(event, dict))
        candidates.extend(self._build_simulation_greeting_events())
        deduped = self._dedupe_proactive_events(candidates)
        ranked = sorted(
            deduped,
            key=lambda item: self._event_priority(item),
        )
        base_target = max(3, min(8, int(round(max(2.0, self._soft_daily_target(user) + 1.2)))))
        selected: list[dict[str, Any]] = []
        used_buckets: set[str] = set()
        for item in ranked:
            bucket = self._simulation_event_bucket(item)
            if bucket in used_buckets:
                continue
            selected.append(item)
            used_buckets.add(bucket)
            if len(selected) >= base_target:
                break
        if not selected:
            selected = [dict(item) for item in _SIMULATION_FALLBACK_EVENTS]
        selected = [dict(item) for item in selected]
        for item in selected:
            item["motive"] = self._normalize_event_motive(item)
        start_ts = _engine_host._now_ts() + 30
        total = len(selected)
        if total == 1:
            schedule_points = [start_ts + 120]
        else:
            last_ts = start_ts + max(18 * 60, duration_minutes * 60 - 120)
            schedule_points = []
            for index in range(total):
                ratio = index / max(1, total - 1)
                base = start_ts + (last_ts - start_ts) * ratio
                jitter = _engine_host.random.uniform(-70, 95)
                schedule_points.append(max(start_ts + index * 70, base + jitter))
            schedule_points.sort()
        events: list[dict[str, Any]] = []
        for item, scheduled in zip(selected, schedule_points):
            cloned = dict(item)
            cloned["_scheduled_ts"] = scheduled
            cloned["_simulated_window"] = str(item.get("window") or "")
            events.append(cloned)
        return events

    def _simulation_event_bucket(self, item: dict[str, Any]) -> str:
        reason = str(item.get("reason") or "")
        if reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return reason
        window = str(item.get("window") or "")
        start, _ = self._parse_window_minutes(window)
        if start is None:
            return f"{reason}|misc"
        if start < 11 * 60:
            daypart = "morning"
        elif start < 15 * 60:
            daypart = "noon"
        elif start < 19 * 60:
            daypart = "evening"
        else:
            daypart = "night"
        topic = _single_line(item.get("topic"), 30)
        return f"{reason}|{daypart}|{topic}"

    async def _test_proactive_action(
        self,
        user: dict[str, Any],
        *,
        action_name: str,
        reason: str,
    ) -> tuple[str, str, list[Any]]:
        name = str(user.get("nickname") or runtime_persona_setting(self, "default_nickname", "你"))
        motive = self._choose_proactive_motive(reason, user, action=action_name)
        action_payload = await self._execute_proactive_action(action_name, user, name, reason)
        action_context = str(action_payload.get("context") or "")
        extra_components = list(action_payload.get("extra_components") or [])
        image_path = self._extract_action_image_path(action_context)
        narrated = await self._narrate_action_context(action_name, action_context)
        if image_path:
            narrated = f"{narrated}\n真实图片文件：{image_path}".strip()
        text = await self._generate_proactive_message_with_llm(
            user, name, reason, narrated, action=action_name, motive=motive
        )
        if not text:
            text = ""
        failure_note = ""
        if not bool(action_payload.get("success", True)):
            failure_note = "\n结果：本次真实主动行为失败；后台正常触发时会直接放弃,不会硬发。"
        return (
            "测试完成：\n"
            f"行为：{action_name}\n"
            f"动机：{motive}\n"
            f"转述：{_single_line(narrated, 180)}\n"
            f"最终消息：\n{text}{failure_note}"
        ), image_path, extra_components
