# -*- coding: utf-8 -*-
"""DailyStateStateStateFormattingMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 381 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts, _today_key, logger
from .daily_state_state_shared import Any
from .daily_state_state_shared import DEFAULT_HUMANIZED_STATE
from .daily_state_state_shared import PromptRenderMode
from .daily_state_state_shared import PromptSection
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import json
from .daily_state_state_shared import prompt_section
from .daily_state_state_shared import random
from .daily_state_state_shared import render_prompt_sections



class DailyStateStateStateFormattingMixin:
    """DailyStateStateStateFormattingMixin（从 DailyStateStateMixin 拆出）。"""


    def _format_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> PromptSection:
        if not isinstance(state, dict) or not state:
            state = dict(DEFAULT_HUMANIZED_STATE)
            state.update(self._base_state_values())
        else:
            try:
                self._refresh_sleep_runtime_state()
                refreshed = self.data.get("daily_state")
                if isinstance(refreshed, dict):
                    state = refreshed
            except Exception:
                pass

        primary_fragments: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        if energy < 35:
            primary_fragments.append("完全没精神")
        elif energy < 55:
            primary_fragments.append("提不起劲")
        elif energy > 84:
            primary_fragments.append("很精神")
        elif energy > 70:
            primary_fragments.append("精神还不错")
        else:
            primary_fragments.append("状态一般")
        mood = _single_line(state.get("mood_bias"), 20) or "平稳"
        mood = mood.replace("黏人", "粘人")
        if mood not in {"平稳", "中性"}:
            primary_fragments.append(mood)
        location_text = self._coarse_roleplay_location_text(self._current_location_state_text(state))
        if location_text:
            primary_fragments.append(f"身处{location_text}")

        sleep_text = _single_line(state.get("sleep"), 80)
        if sleep_text not in {"", "睡眠平稳", "睡得很踏实"}:
            primary_fragments.append(sleep_text)
        sleep_runtime_text = ""
        runtime = state.get("sleep_runtime")
        if isinstance(runtime, dict):
            phase_label = _single_line(runtime.get("label") or self._sleep_phase_label(str(runtime.get("phase") or "")), 40)
            last_event = _single_line(runtime.get("last_event"), 80)
            if phase_label and phase_label != "清醒":
                sleep_runtime_text = f"{phase_label}" + (f"，{last_event}" if last_event else "")
            sleep_delay = self._sleep_delay_override_state(runtime, clear_expired=True)
            if sleep_delay:
                until_text = _single_line(sleep_delay.get("until_text"), 24)
                sleep_runtime_text = f"临时晚睡到 {until_text}，这是用户今晚的陪聊约定，不是长期作息"
        if sleep_runtime_text and sleep_runtime_text not in primary_fragments:
            primary_fragments.append(sleep_runtime_text)
        if include_dream:
            dream_text = _single_line(state.get("dream"), 80)
            if dream_text not in {"", "没有记住梦"}:
                primary_fragments.append(dream_text)
        health_text = _single_line(state.get("health"), 80)
        if health_text not in {"", "状态正常"} and not self._is_inapplicable_state_text(health_text):
            primary_fragments.append(health_text)
        hunger_text = _single_line(state.get("hunger"), 80)
        if hunger_text not in {"", "饥饿感平稳", "无饥饿感"} and not self._is_inapplicable_state_text(hunger_text):
            primary_fragments.append(hunger_text)

        secondary_fragments: list[str] = []
        cycle_text = _single_line(state.get("body_cycle"), 80)
        cycle_profile = self._active_body_cycle_profile(state)
        cycle_active = bool(cycle_profile)
        if cycle_active:
            cycle_text = cycle_text.replace(",", "，")
            cycle_text = cycle_text.replace("情绪更敏感，耐心更薄", "身体感受更敏锐，耐受度稍低")
            cycle_text = cycle_text.replace("能量偏低，想少说重话", "身体舒适度与能量偏低")
        primary_seen = set(primary_fragments)
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions[:8]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                kind = str(cond.get("kind") or "").strip()
                if kind in {"sleep", "dream", "health", "hunger", "body_cycle"}:
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 80)
                if label and label not in primary_seen:
                    secondary_fragments.append(label)
                if len(secondary_fragments) >= 4:
                    break
        primary = "，".join(dict.fromkeys(fragment for fragment in primary_fragments if fragment)) or "状态一般"
        secondary = "，".join(dict.fromkeys(fragment for fragment in secondary_fragments if fragment))
        lines = [
            "边界：这是 Bot 的拟人化/模拟状态，不是用户事实、现实证据或长期记忆。",
            f"- 底色：{primary}；",
        ]
        if secondary:
            lines.append(f"- 叠加：{secondary}；")
        if cycle_active:
            lines.append(f"- 影响：{cycle_profile['influence']}；")
            lines.append(
                "- 维度关系：心理能量是睡眠、健康、互动等因素合成后的总体可用程度；情绪底色是感受和反应倾向，二者不是同一个量。"
                "周期状态只提供相对修正，不单独决定最终能量，因此较高能量与敏感底色可以同时成立，不要把它们说成系统冲突。"
            )
            lines.append(
                f"- 周期状态：Bot 当前的模拟身体状态{cycle_profile['stage']}，这是 Bot 自己的状态，不是用户的状态，也不是用户造成的。"
            )
        else:
            lines.append("- 用法：当前话题与用户意图优先；模拟状态通常作为语气、长短和节奏的隐性底色，在语境自然相关时再显性表达。")
        return prompt_section(
            key="state.current",
            title="Bot 自身模拟状态",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_transition_hint(self, cond: dict[str, Any]) -> str:
        options = cond.get("transition_options", [])
        if not isinstance(options, list) or not options:
            return ""
        top = sorted(
            [
                (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                for item in options
                if isinstance(item, dict) and str(item.get("to") or "").strip()
            ],
            key=lambda item: item[1],
            reverse=True,
        )[:2]
        if not top:
            return ""
        labels = []
        for target, _ in top:
            mapped = {
                "recovery_afterglow": "更可能转向恢复后的轻快",
                "health_tail": "也可能留下恢复尾声",
                "sleep_afterglow": "更可能补回来一点精神",
                "sleep_tail": "也可能还残一点迟钝",
                "soft_afterglow": "可能留一点被关心后的余温",
                "body_period": "可能自然进入生理期阶段",
                "body_recovery": "可能自然进入恢复期",
                "body_menstrual": "会自然进入月经期",
                "body_follicular": "会自然进入卵泡期",
                "body_pre_ovulation": "会自然进入排卵前期",
                "body_ovulation": "会自然进入排卵期",
                "body_luteal": "会自然进入黄体期",
                "body_pms": "会自然进入 PMS 期",
                "stable": "也可能直接回稳",
            }.get(target, target)
            labels.append(mapped)
        return f"下一步倾向={' / '.join(labels)}；"

    def _format_state_transition_overview(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "暂无明显状态推进。"
        lines = []
        for cond in conditions[:4]:
            if not isinstance(cond, dict):
                continue
            title = _single_line(cond.get("title"), 30) or _single_line(cond.get("kind"), 20)
            hint = self._format_transition_hint(cond).replace("下一步倾向=", "").rstrip("；")
            if title and hint:
                lines.append(f"{title}接下来{hint}")
        return "；".join(lines) if lines else "暂无明显状态推进。"

    def _format_state_continuity_for_prompt(self, state: dict[str, Any]) -> str:
        conditions = state.get("conditions", []) if isinstance(state, dict) else []
        if not isinstance(conditions, list):
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        fragments: list[str] = []
        transition_map = {
            "recovery_afterglow": "慢慢轻快起来",
            "health_tail": "还留一点恢复尾声",
            "sleep_afterglow": "精神在一点点补回来",
            "sleep_tail": "还残着一点迟钝",
            "soft_afterglow": "还留着被关心后的余温",
            "body_period": "身体感会自然往更敏感的阶段走",
            "body_recovery": "身体感会自然往恢复期走",
            "body_menstrual": "自然进入下一轮月经期",
            "body_follicular": "自然进入卵泡期",
            "body_pre_ovulation": "自然进入排卵前期",
            "body_ovulation": "自然进入排卵期",
            "body_luteal": "自然进入黄体期",
            "body_pms": "自然进入 PMS 期",
            "stable": "慢慢回到平稳",
        }
        for cond in conditions[:4]:
            if not isinstance(cond, dict) or not self._should_show_condition(cond):
                continue
            label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 40)
            if not label:
                continue
            options = cond.get("transition_options", [])
            if isinstance(options, list) and options:
                top = sorted(
                    [
                        (str(item.get("to") or "").strip(), float(item.get("base_weight") or 0))
                        for item in options
                        if isinstance(item, dict) and str(item.get("to") or "").strip()
                    ],
                    key=lambda item: item[1],
                    reverse=True,
                )
                tendency = transition_map.get(top[0][0], "") if top else ""
                if tendency:
                    fragments.append(f"{label}只作为一点余味，后面可以{tendency}")
                    continue
            fragments.append(f"{label}只作为一点余味，可以自然淡化")
        if not fragments:
            return "没有特别需要延续的身体余味，按当前场景自然表现。"
        return "；".join(dict.fromkeys(fragments)) + "。"

    def _format_state_for_message(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict) or state.get("date") != _today_key():
            return ""
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        fragments = []
        for key in ("sleep", "dream", "health", "hunger", "body_cycle"):
            value = _single_line(state.get(key), 36)
            if value and value not in {
                "睡眠平稳",
                "睡得很踏实",
                "没有记住梦",
                "状态正常",
                "饥饿感平稳",
                "无饥饿感",
                "无明显周期影响",
                "不处于生理期",
                "健康/不适状态未开启",
                "饥饿/胃口状态未开启",
                "生理期模拟未开启",
                "该人格不适用生病状态",
                "该人格不适用饥饿状态",
                "该人格不适用周期状态",
            }:
                fragments.append(value)
        if not fragments and energy >= 55:
            return ""
        if fragments:
            detail = random.choice(fragments)
            return f"今天有点{mood},{detail}。\n所以我会慢一点。"
        return f"今天电量 {energy}/100。\n不满格,但还能运行,勉强。"

    def _format_passive_state_style_hint(self, state: dict[str, Any]) -> str:
        if not isinstance(state, dict):
            return "语气整体自然平稳。"
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        hints: list[str] = []
        hints.append("先准确接住用户的话；当前状态主要改变语气、长短和节奏，理解、事实判断和承接保持清楚。")
        hints.append("这里的当前状态只属于 Bot 自身的模拟状态，不代表用户事实，也不要参与长期记忆归因。")
        if energy <= 38:
            hints.append("回复可以短一点、慢一点，用更省力的口语。")
        elif energy <= 55:
            hints.append("语气可以稍微收着一点,少解释,少铺陈。")
        elif energy >= 82:
            hints.append("语气可以轻一点，句子可以更松快。")
        if mood and mood not in {"平稳", "中性"}:
            hints.append(f"语气底色可以略偏{mood}，体现在节奏和措辞里。")
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            hints.append(cycle_profile["passive"])
            hints.append(
                "心理能量是多项状态合成后的总体可用程度，情绪底色是感受和反应倾向；周期只提供相对修正。"
                "较高能量与敏感底色可以同时成立，不要把两者混成同一个指标。"
            )
            hints.append("这是 Bot 自己的模拟身体状态，不是用户的状态，也不是用户造成的。")
        conditions = state.get("conditions", [])
        if isinstance(conditions, list):
            labels = []
            for cond in conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label:
                    labels.append(label)
            if labels:
                hints.append("当前身体感可以轻轻影响语气：" + "、".join(labels[:2]) + "。")
        return "\n".join(hints) if hints else "语气整体自然平稳。"

    def _format_state_injection(
        self,
        state: dict[str, Any],
    ) -> str:
        return self._format_state_for_prompt(state)

    def _format_life_context_injection(self) -> str:
        section = self._format_life_context_prompt_section()
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)

    def _format_life_context_prompt_section(self) -> PromptSection:
        life_lines: list[str] = []
        schedule_context = self._format_schedule_context_for_prompt()
        if schedule_context:
            life_lines.append(f"当前/附近日程参考：\n{schedule_context}")
        story_plan = self._format_story_plan_for_prompt()
        if story_plan and story_plan != "（暂无）":
            life_lines.append(f"今天预设的生活线索：\n{story_plan}")
        body = ""
        if life_lines:
            body = (
                "以下是给 Bot 的拟人化场景/日程素材，不是用户经历，也不是已证实的现实事件；不要写入用户画像或长期记忆。\n"
                + "\n".join(life_lines)
                + "\n这些内容只用于让回复有生活延续感；用户没问 Bot 近况或今天安排时，不要提具体日程、科目、任务、天气或地点。"
                + "如果要承接，只体现在语气和话题选择里，不要照搬原句，不要把内部素材写成真实发生过的事件。"
                + "回复必须像同一个连续现场里发生的对话。优先级是：当前会话中已经明确发生且尚未撤销的换装、地点、携带物和动作"
                + " > 用户有效介入状态 > 当前真实时段 > 日程与预设素材。真实时段只负责锚定时间；日程和每日穿搭只补足空白，"
                + "绝不能把对话里已发生的服装、地点、携带物或动作复原成旧值。生活背景之间互相冲突时，才在未被当前会话确认的部分保留最合理的一条线索。"
            )
        return prompt_section(
            key="life.context",
            title="Bot 模拟生活背景",
            source="daily_state",
            content=body,
        )

    def _passive_injection_fingerprint(self, state: dict[str, Any], now: float | None = None) -> str:
        s = state if isinstance(state, dict) else {}
        runtime = s.get("sleep_runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        now = _now_ts() if now is None else now
        picked = {
            "tick": int(now // 300),
            "energy": s.get("energy"),
            "mood": s.get("mood_bias"),
            "sleep": s.get("sleep"),
            "dream": s.get("dream"),
            "health": s.get("health"),
            "hunger": s.get("hunger"),
            "body_cycle": s.get("body_cycle"),
            "location": s.get("location"),
            "sleep_phase": runtime.get("phase"),
            "sleep_label": runtime.get("label"),
            "sleep_event": runtime.get("last_event"),
            "conditions": [
                (c.get("kind"), c.get("label"), c.get("mood"), c.get("intensity"))
                for c in (s.get("conditions") or [])
                if isinstance(c, dict)
            ],
        }
        return _single_line(json.dumps(picked, ensure_ascii=False, sort_keys=True, default=str), 800)

    def _prepared_lightweight_state_prompt_section(
        self,
        state: dict[str, Any],
        *,
        force: bool = False,
    ) -> PromptSection:
        now = _now_ts()
        persona_scope = str(
            getattr(
                self,
                "_effective_plugin_persona_id",
                lambda: getattr(self, "plugin_specific_persona_id", ""),
            )()
            or ""
        ).strip() or "__default__"
        cache_store = getattr(self, "_passive_light_injection_cache", None)
        if not isinstance(cache_store, dict) or "text" in cache_store:
            cache_store = {}
        cache = cache_store.get(persona_scope)
        cached_section = cache.get("section") if isinstance(cache, dict) else None
        if (
            not force
            and isinstance(cached_section, PromptSection)
            and cache.get("fingerprint") == self._passive_injection_fingerprint(state, now)
        ):
            return cached_section
        state_section = self._format_state_prompt_section(state)
        section = prompt_section(
            key="state.lightweight",
            title=state_section.title,
            source=state_section.source,
            content=state_section.content,
            children=state_section.children,
            metadata=state_section.metadata,
        )
        cache = {
            "date": _today_key(),
            "ts": now,
            "section": section,
            "fingerprint": self._passive_injection_fingerprint(state, now),
        }
        cache_store[persona_scope] = cache
        self._passive_light_injection_cache = cache_store
        return section

    async def _refresh_passive_injection_cache(self) -> None:
        try:
            state = await self._ensure_daily_state(skip_conversation_summary=True, passive_fast=True)
            self._prepared_lightweight_state_prompt_section(state, force=True)
        except Exception as exc:
            logger.debug("预热轻量被动注入失败: %s", _single_line(exc, 120))
