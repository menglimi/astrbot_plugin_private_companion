# -*- coding: utf-8 -*-
"""DailyStateStateHealthManualWarmthMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（9 个方法 + 0 个模块级名字 + 0 个类级赋值 / 292 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts
from .daily_state_state_shared import Any
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import random
from .daily_state_state_shared import re



class DailyStateStateHealthManualWarmthMixin:
    """DailyStateStateHealthManualWarmthMixin（从 DailyStateStateMixin 拆出）。"""


    def _build_health_causes(
        self,
        *,
        sleep_label: str,
        weather_text: str,
        diary_tags: set[str],
    ) -> list[str]:
        causes: list[str] = []
        if sleep_label not in {"睡眠平稳", "睡得很踏实"} and random.random() < 0.7:
            causes.append("昨晚没睡踏实")
        if any(tag in diary_tags for tag in {"失眠", "低能量"}) and random.random() < 0.45:
            causes.append("前一天状态就有点透支")
        weather_lower = str(weather_text or "").lower()
        if any(token in weather_text for token in ("降雨", "小雨", "中雨", "大雨", "阴", "多云")) and random.random() < 0.4:
            causes.append("空气有点潮,身上那股乏劲更明显")
        if any(token in weather_text for token in ("风", "降温", "冷")) and random.random() < 0.55:
            causes.append("吹了点风,身上容易发空")
        temp_match = re.search(r"(-?\d+(?:\.\d+)?)\s*°C", weather_lower)
        if temp_match:
            try:
                temp = float(temp_match.group(1))
            except ValueError:
                temp = 20.0
            if temp <= 10 and random.random() < 0.55:
                causes.append("天气偏冷,早上容易着凉")
            elif temp >= 30 and random.random() < 0.35:
                causes.append("天气闷热,整个人有点蔫")
        return causes

    def _pick_health_spec(
        self, causes: list[str], intensity: float, weather_text: str
    ) -> tuple[str, str, int, int, str] | None:
        if not causes:
            return None
        chance = min(0.42, 0.12 + len(causes) * 0.1 * max(0.5, intensity))
        if random.random() > chance:
            return None
        cause_text = ",".join(dict.fromkeys(causes[:2]))
        pool = [
            ("喉咙有点发紧,今天想少说重话", "安静", -10, 24),
            ("头有点沉,做事想放慢一点", "疲惫", -14, 18),
            ("像有点发虚,反应会慢半拍", "疲惫", -18, 30),
        ]
        label, mood, energy_delta, duration_hours = random.choice(pool)
        if "闷热" in cause_text and "喉咙" in label:
            label = "有点发闷,只想把动作放轻一点"
        if "潮" in cause_text and "头有点沉" in label:
            label = "身上有点沉,今天想把事情做轻一点"
        return label, mood, energy_delta, duration_hours, cause_text

    def _build_transition_options(
        self,
        *,
        kind: str,
        energy_delta: int,
        cause: str,
        on_end_transition: str,
    ) -> list[dict[str, Any]]:
        if on_end_transition == "health_relief":
            return [
                {"to": "recovery_afterglow", "base_weight": 0.45},
                {"to": "stable", "base_weight": 0.4},
                {"to": "health_tail", "base_weight": 0.15},
            ]
        if on_end_transition == "sleep_rebound":
            return [
                {"to": "sleep_afterglow", "base_weight": 0.35},
                {"to": "stable", "base_weight": 0.5},
                {"to": "sleep_tail", "base_weight": 0.15},
            ]
        if kind == "care_warmth":
            return [
                {"to": "stable", "base_weight": 0.8},
                {"to": "soft_afterglow", "base_weight": 0.2},
            ]
        return []

    def _make_condition(
        self,
        *,
        kind: str,
        title: str,
        label: str,
        mood: str,
        energy_delta: int,
        duration_hours: int,
        intensity: int,
        cause: str = "",
        on_end_transition: str = "",
        phase: str = "",
        episode_key: str = "",
        transition_options: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        start_ts = _now_ts()
        return {
            "id": f"{kind}-{int(start_ts)}-{random.randint(1000, 9999)}",
            "kind": kind,
            "title": title,
            "label": label,
            "mood": mood,
            "energy_delta": energy_delta,
            "intensity": intensity,
            "start_ts": start_ts,
            "end_ts": start_ts + duration_hours * 3600,
            "duration_hours": duration_hours,
            "cause": cause,
            "on_end_transition": on_end_transition,
            "phase": phase,
            "episode_key": episode_key,
            "transition_options": list(transition_options or []),
        }

    def _infer_manual_state_mood(self, text: str) -> str:
        raw = str(text or "")
        mapping = [
            (("累", "疲惫", "困", "没电"), "疲惫"),
            (("烦", "乱", "躁", "闷"), "烦闷"),
            (("病", "难受", "不舒服", "头疼", "发烧"), "虚弱"),
            (("饿", "胃口", "嘴馋"), "黏人"),
            (("开心", "轻快", "高兴", "兴奋"), "轻快"),
            (("紧张", "慌", "忐忑"), "紧张"),
            (("安静", "困倦", "恍惚"), "安静"),
        ]
        for markers, mood in mapping:
            if any(marker in raw for marker in markers):
                return mood
        return "平稳"

    def _infer_manual_state_energy_delta(self, text: str) -> int:
        raw = str(text or "")
        if any(token in raw for token in ("开心", "轻快", "高兴", "兴奋")):
            return 6
        if any(token in raw for token in ("病", "难受", "不舒服", "发烧", "头疼")):
            return -16
        if any(token in raw for token in ("累", "疲惫", "困", "没电")):
            return -10
        if any(token in raw for token in ("烦", "乱", "躁", "闷")):
            return -8
        return -4 if any(token in raw for token in ("紧张", "慌")) else 0

    async def _add_manual_state(self, value: str) -> tuple[bool, str]:
        raw = str(value or "").strip()
        if not raw:
            return False, "请这样填写：陪伴 增添状态 有点累了|8"
        label_part, sep, hours_part = raw.partition("|")
        label = _single_line(label_part, 80)
        if not label:
            return False, "状态描述不能为空。"
        profile = self._persona_state_profile()
        hunger_like = any(token in label for token in ("饿", "胃口", "嘴馋", "馋", "想吃", "吃点", "吃些"))
        health_like = any(token in label for token in ("病", "难受", "不舒服", "发烧", "头疼", "头痛", "咳", "感冒"))
        if hunger_like and not profile.get("allow_hunger", True):
            return False, "当前配置未开启饥饿/胃口状态。"
        if health_like and not profile.get("allow_health", True):
            return False, "当前配置未开启健康/不适状态。"
        duration_hours = _safe_int(hours_part.strip() if sep else 12, 12, 1, 72)
        mood = self._infer_manual_state_mood(label)
        energy_delta = self._infer_manual_state_energy_delta(label)
        await self._ensure_daily_state()
        async with self._data_lock:
            conditions = self.data.setdefault("state_conditions", [])
            if not isinstance(conditions, list):
                self.data["state_conditions"] = []
                conditions = self.data["state_conditions"]
            conditions.append(
                self._make_condition(
                    kind="manual_state",
                    title="手动增添状态",
                    label=label,
                    mood=mood,
                    energy_delta=energy_delta,
                    duration_hours=duration_hours,
                    intensity=60,
                    cause="由用户手动增添",
                    phase="manual",
                )
            )
            state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
            self.data["daily_state"] = state
            self._save_data_sync(sections={"daily_state", "state_conditions"})
        return True, f"已增添状态：{label}（约持续 {duration_hours} 小时）"

    def _detect_interaction_warmth_feedback(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_warmth": False}
        intimate = bool(re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|抱一下|贴一下|rua", normalized, re.IGNORECASE))
        comfort = bool(re.search(r"陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下|摸摸头", normalized))
        positive = bool(re.search(r"开心|好耶|哈哈|笑死|可爱|喜欢|太好了|真好|想你|爱你|在呢|来了|陪我", normalized, re.IGNORECASE))
        if not (intimate or comfort or positive):
            return {"is_warmth": False}

        relationship_score = _safe_int(user.get("relationship_score") if isinstance(user, dict) else 0, 0, 0)
        episode_count = _safe_int(user.get("episode_message_count") if isinstance(user, dict) else 0, 0, 0)
        state = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        energy = _safe_int(state.get("energy"), 75, 0, 100)

        is_sustained_positive = positive and episode_count >= 6 and relationship_score >= 18
        if positive and not (intimate or comfort or is_sustained_positive):
            return {"is_warmth": False}

        base_delta = 2 if intimate or comfort else 1
        if energy <= 45:
            base_delta += 2
        elif energy <= 62:
            base_delta += 1
        elif energy >= 86:
            base_delta = max(1, base_delta - 1)
        if relationship_score >= 120:
            base_delta += 2
        elif relationship_score >= 55:
            base_delta += 1
        if is_sustained_positive and episode_count >= 10:
            base_delta += 1

        max_delta = 8 if intimate or comfort else 4
        delta = max(1, min(max_delta, base_delta))
        if intimate:
            source = "亲密互动回暖"
            label = "被亲近安抚后,精神轻轻回暖"
            mood = "柔和"
            duration_hours = 4
            intensity = 58
            phase = "intimacy"
        elif comfort:
            source = "安慰互动回暖"
            label = "被安慰后,紧绷感松开一点"
            mood = "柔和"
            duration_hours = 4
            intensity = 54
            phase = "comfort"
        else:
            source = "连续对话回暖"
            label = "和熟悉的人连续聊了一会儿,精神被带起来一点"
            mood = "轻快"
            duration_hours = 3
            intensity = 42
            phase = "sustained_positive_chat"
        return {
            "is_warmth": True,
            "source": source,
            "label": label,
            "mood": mood,
            "energy_delta": delta,
            "duration_hours": duration_hours,
            "intensity": intensity,
            "phase": phase,
            "cause": _single_line(normalized, 80),
            "max_delta": max_delta,
        }

    def _apply_interaction_warmth_to_state(self, text: str, user: dict[str, Any] | None = None) -> bool:
        feedback = self._detect_interaction_warmth_feedback(text, user)
        if not feedback.get("is_warmth"):
            return False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        max_delta = _safe_int(feedback.get("max_delta"), 6, 1, 10)
        active = next(
            (
                cond for cond in reversed(conditions)
                if isinstance(cond, dict)
                and str(cond.get("kind") or "") == "interaction_warmth"
                and _safe_float(cond.get("end_ts"), 0) > now
            ),
            None,
        )
        if isinstance(active, dict):
            current_delta = _safe_int(active.get("energy_delta"), 0, 0, 20)
            incoming_delta = _safe_int(feedback.get("energy_delta"), 1, 1, 10)
            active["energy_delta"] = min(max_delta, max(current_delta, incoming_delta) + 1)
            active["end_ts"] = max(
                _safe_float(active.get("end_ts"), now),
                now + _safe_int(feedback.get("duration_hours"), 3, 1, 8) * 3600,
            )
            active["duration_hours"] = max(1, int((_safe_float(active.get("end_ts"), now) - now) / 3600))
            active["label"] = _single_line(feedback.get("label"), 80)
            active["mood"] = _single_line(feedback.get("mood"), 20) or active.get("mood") or "柔和"
            active["cause"] = _single_line(feedback.get("cause"), 80)
            active["phase"] = _single_line(feedback.get("phase"), 40)
            active["intensity"] = max(_safe_int(active.get("intensity"), 40), _safe_int(feedback.get("intensity"), 40))
        else:
            conditions.append(
                self._make_condition(
                    kind="interaction_warmth",
                    title=_single_line(feedback.get("source"), 40) or "互动回暖",
                    label=_single_line(feedback.get("label"), 80),
                    mood=_single_line(feedback.get("mood"), 20) or "柔和",
                    energy_delta=_safe_int(feedback.get("energy_delta"), 2, 1, 10),
                    duration_hours=_safe_int(feedback.get("duration_hours"), 3, 1, 8),
                    intensity=_safe_int(feedback.get("intensity"), 45, 0, 100),
                    cause=_single_line(feedback.get("cause"), 80),
                    phase=_single_line(feedback.get("phase"), 40),
                )
            )
        self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return True
