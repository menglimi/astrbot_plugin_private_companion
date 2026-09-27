# -*- coding: utf-8 -*-
"""EventDispatchResultProactivePart03Mixin。

由 tools/split_mixin_domain.py 从 event_dispatch_result_proactive.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 129 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 EventDispatchResultProactiveMixin）。
"""
from __future__ import annotations
from .event_dispatch_result_proactive_shared import Any
from .event_dispatch_result_proactive_shared import _persona_value
from .event_dispatch_result_proactive_shared import _safe_float
from .event_dispatch_result_proactive_shared import _safe_int
from .event_dispatch_result_proactive_shared import _single_line
from .event_dispatch_result_proactive_shared import math
from .event_dispatch_result_proactive_shared import random



class EventDispatchResultProactivePart03Mixin:
    """EventDispatchResultProactivePart03Mixin（从 EventDispatchResultProactiveMixin 拆出）。"""


    async def _calc_segmented_proactive_interval(
        self,
        text: str,
        *,
        event: Any | None = None,
        umo: str = "",
        chat_type: str = "",
    ) -> float:
        setting_getter = getattr(self, "_segmented_setting", None)

        def setting(name: str, default: Any) -> Any:
            if not callable(setting_getter):
                return getattr(self, f"segmented_proactive_{name}", default)
            return setting_getter(
                name,
                event=event,
                umo=umo,
                chat_type=chat_type,
                default=default,
            )

        interval_method = str(setting("interval_method", "log") or "log").strip().lower()
        if interval_method == "log":
            if all(ord(ch) < 128 for ch in text):
                word_count = len(text.split())
            else:
                word_count = len([ch for ch in text if ch.isalnum()])
            log_base = max(1.1, _safe_float(setting("log_base", 1.8), 1.8, 1.1))
            interval = math.log(word_count + 1, log_base)
            return random.uniform(interval, interval + 0.5)
        interval_min = max(0.1, _safe_float(setting("interval_min", 1.5), 1.5, 0.1))
        interval_max = max(interval_min, _safe_float(setting("interval_max", 3.5), 3.5, 0.1))
        return random.uniform(
            interval_min,
            interval_max,
        )

    def _event_topic_signature(self, event: dict[str, Any] | None) -> str:
        if not isinstance(event, dict):
            return ""
        return self._proactive_topic_signature(
            event.get("topic"),
            event.get("motive"),
            event.get("why"),
            event.get("scene"),
            event.get("impulse"),
        )

    def _apply_group_wakeup_to_humanized_state(self, scene: dict[str, Any], text: str) -> dict[str, Any]:
        if not _persona_value(self, 'enable_humanized_states', True) or not isinstance(scene, dict):
            return {}
        trigger = str(scene.get("trigger") or "")
        if not trigger.startswith("group_wakeup_"):
            return {}
        state = self.data.setdefault("daily_state", {})
        if not isinstance(state, dict):
            state = {}
            self.data["daily_state"] = state
        try:
            runtime = self._refresh_sleep_runtime_state()
        except Exception:
            runtime = self._sleep_runtime_state()
        phase = str(runtime.get("phase") or "")
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        word = _single_line(scene.get("wakeup_word"), 60)
        wakeup_type = trigger.replace("group_wakeup_", "")
        strength = _single_line(scene.get("wakeup_strength"), 24) or self._group_wakeup_strength(wakeup_type, {}, scene)
        strength_label = self._group_wakeup_strength_label(strength)
        fatigue = scene.get("wakeup_fatigue") if isinstance(scene.get("wakeup_fatigue"), dict) else {}
        fatigue_label = _single_line(fatigue.get("label"), 20)
        fatigue_suffix = f"；最近群聊唤醒疲劳为{fatigue_label},回应应更省力" if str(fatigue.get("level") or "") in {"medium", "high"} else ""
        current_getter = getattr(self, "_agenda_current_context_item", None)
        legacy_getter = getattr(self, "_get_current_plan_item", None)
        try:
            current_item = (
                current_getter()
                if callable(current_getter)
                else legacy_getter(self.data.get("daily_plan", {}))
                if callable(legacy_getter)
                else None
            )
        except Exception:
            current_item = None
        is_sleep_phase = phase in {"falling_asleep", "light_sleep", "sleeping_again", "woken"} or bool(self._is_sleepy_plan_item(current_item))
        if is_sleep_phase:
            runtime = self._mark_sleep_woken_by_group_wakeup(text, wakeup_type=wakeup_type)
            prior = _safe_int(runtime.get("woken_count"), 1, 1)
            state_note = (
                "当前处在睡眠/休息段,群聊唤醒把她从睡意里轻轻拽起来；回复应短、慢半拍,带一点刚醒的迷糊语气,但必须看清上下文再回应。"
                if prior <= 1
                else "当前处在睡眠/休息段且已被多次叫醒；回复更应短、慢,语气像半梦半醒,不要突然精神饱满,但不要答非所问或乱接。"
            ) + fatigue_suffix
            updates = [
                "清醒程度：睡眠/休息中被群聊唤醒",
                "语气：短、轻、慢半拍",
                "后续安排：群里不继续叫她就继续睡回去",
                f"唤醒强度：{strength_label}",
            ]
            self._record_group_wakeup_state_adjustment(
                scene=scene,
                text=text,
                state_note=state_note,
                updates=updates,
                intensity="中",
                carry_rule="群聊回复必须承接睡眠中被叫醒的语气感觉,但不得降低上下文理解和回答质量；如果后续没有继续对她说话,后续细化应让她继续休息或睡回去。",
            )
            return {"note": state_note, "updates": updates, "sleep_phase": runtime.get("label"), "intensity": "中", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}
        if "interest" in trigger:
            if energy <= 38:
                state_note = "当前能量偏低,但群里碰到她感兴趣的话题；会有一点被勾起的精神,仍然不要长篇抢话。"
                updates = ["兴趣：被群聊话题勾起", "能量：低电量中轻微回亮", "回复策略：短句接话,不抢主导权"]
            else:
                state_note = "群里碰到她感兴趣的话题；她可以像被话题勾住一样自然冒头,但仍要尊重原本群聊走向。"
                updates = ["兴趣：上升", "分享欲：小幅上升", "回复策略：轻轻接话"]
            state_note += fatigue_suffix
            updates.append(f"唤醒强度：{strength_label}")
            self._record_group_wakeup_state_adjustment(scene=scene, text=text, state_note=state_note, updates=updates, intensity="轻")
            return {"note": state_note, "updates": updates, "interest_word": word, "intensity": "轻", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}
        if energy <= 38:
            state_note = "当前能量偏低,群里叫到她时会反应慢一点；可以回应,但应更短、更省力。"
            updates = ["清醒/注意力：被群里叫回一点", "语气：省力、短句", "主动欲：不额外扩张"]
        elif energy >= 82:
            state_note = "当前状态偏有精神,群里叫到她时可以更快接住,但仍不要像主持人一样抢话。"
            updates = ["注意力：快速转向群聊", "语气：更轻快", "回复策略：自然接一句"]
        else:
            state_note = "群里提到她或出现需要她接话的词；她会把注意力从当前日程挪到群聊里,像被自然叫到。"
            updates = ["注意力：转向群聊", "回复姿态：被叫到后自然接话", "边界：不暴露触发逻辑"]
        state_note += fatigue_suffix
        updates.append(f"唤醒强度：{strength_label}")
        self._record_group_wakeup_state_adjustment(scene=scene, text=text, state_note=state_note, updates=updates, intensity="轻")
        return {"note": state_note, "updates": updates, "wakeup_word": word, "intensity": "轻", "strength": strength, "strength_label": strength_label, "fatigue": fatigue}
