# -*- coding: utf-8 -*-
"""DailyStateStateDisplayOutfitSnapshotMixin。

由 tools/split_mixin_domain.py 从 daily_state_state.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 356 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateStateMixin）。
"""
from __future__ import annotations

from .daily_state_state_shared import _now_ts, _today_key
from .daily_state_state_shared import Any
from .daily_state_state_shared import PromptRenderMode
from .daily_state_state_shared import PromptSection
from .daily_state_state_shared import _safe_float
from .daily_state_state_shared import _safe_int
from .daily_state_state_shared import _single_line
from .daily_state_state_shared import prompt_section
from .daily_state_state_shared import re
from .daily_state_state_shared import render_prompt_sections
from .daily_state_state_shared import runtime_persona_setting



class DailyStateStateDisplayOutfitSnapshotMixin:
    """DailyStateStateDisplayOutfitSnapshotMixin（从 DailyStateStateMixin 拆出）。"""


    def _is_inapplicable_state_text(self, text: str) -> bool:
        return "不适用" in str(text or "")

    @staticmethod
    def _state_condition_allowed(kind: str, profile: dict[str, bool]) -> bool:
        if kind == "health":
            return bool(profile.get("allow_health", True))
        if kind == "hunger":
            return bool(profile.get("allow_hunger", True))
        if kind in {"body_cycle", "cycle_discomfort"}:
            return bool(profile.get("allow_cycle", False))
        return True

    def _should_show_condition(self, cond: dict[str, Any]) -> bool:
        if not isinstance(cond, dict):
            return False
        if _safe_int(cond.get("energy_delta"), 0) != 0:
            return True
        if _single_line(cond.get("mood"), 20) not in {"", "平稳"}:
            return True
        if cond.get("cause") or cond.get("phase"):
            return True
        return str(cond.get("kind") or "") not in {"sleep", "dream"}

    def _format_can_do_for_prompt(self) -> str:
        items = self.data.get("can_do", [])
        if not isinstance(items, list) or not items:
            return "（暂未设置）"
        lines = []
        for item in items[:30]:
            text = _single_line(item, 80)
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines) if lines else "（暂未设置）"

    @staticmethod
    def _detect_dialogue_outfit_change(text: Any) -> str:
        normalized = _single_line(text, 180)
        if not normalized:
            return ""
        outfit = (
            r"(?:JK(?:制服|服)?|jk(?:制服|服)?|校服|制服|衣服|衣裳|服装|穿搭|套装|"
            r"睡衣|睡裙|睡袍|居家服|礼服|正装|西装|汉服|和服|旗袍|洛丽塔|lo裙|"
            r"女仆装|巫女服|泳装|泳衣|运动服|球衣|外套|风衣|大衣|夹克|衬衫|"
            r"T恤|毛衣|卫衣|上衣|背心|连衣裙|短裙|长裙|裙子|裤子|短裤|袜子|鞋子|帽子|围巾)"
        )
        action = r"(?:换(?:装|衣|上|成|为|掉|下|回|一套|一身|一件|一条|身)?|改穿|穿(?:上|着|了)?|套上|脱下|脱掉)"
        has_outfit_change = bool(
            re.search(rf"{action}.{{0,24}}{outfit}|{outfit}.{{0,12}}{action}", normalized, re.IGNORECASE)
        )
        if not has_outfit_change:
            return ""

        question_or_hypothesis = bool(
            re.search(r"要不要|能不能|可不可以|是否|是不是|想不想|会不会|如果|假如|[？?]", normalized)
        )
        positive_after_boundary = bool(
            re.search(rf"(?:^|[，,。；;！!]\s*)(?:那|现在|然后|再|先|快|去|把|给|来)?\s*(?:你|她)?\s*{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE)
            or re.search(rf"(?:^|[，,。；;！!]\s*)把.{{0,10}}{outfit}.{{0,8}}{action}", normalized, re.IGNORECASE)
        )
        if question_or_hypothesis and not positive_after_boundary:
            return ""

        negated_change = bool(re.search(rf"(?:不要|别|不用|不必|不许|禁止).{{0,8}}{action}", normalized))
        if negated_change and not re.search(rf"[，,。；;！!].{{0,12}}{action}.{{0,24}}{outfit}", normalized, re.IGNORECASE):
            return ""

        direct_target = bool(
            re.search(rf"(?:让|叫|给|帮)?(?:你|她|角色|星缘|bot|机器人).{{0,16}}{action}", normalized, re.IGNORECASE)
        )
        shared_target = bool(re.search(rf"(?:我们|咱们|咱俩).{{0,8}}{action}", normalized))
        imperative = positive_after_boundary
        if not (direct_target or shared_target or imperative):
            return ""

        meta_feedback = bool(
            re.search(r"掉状态|对不上|文本里|文本里面|旧衣服|原本|之前|怎么又|为什么|bug|BUG|问题", normalized)
        )
        if meta_feedback and not imperative:
            return ""
        return normalized

    def _current_dialogue_outfit_override(
        self,
        *,
        user_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        snapshot = data.get("dialogue_outfit_override")
        if not isinstance(snapshot, dict):
            return {}
        check_now = _now_ts() if now is None else now
        if _single_line(snapshot.get("date"), 16) != _today_key():
            return {}
        if _safe_float(snapshot.get("expires_at"), 0) <= check_now:
            return {}
        source_user_id = _single_line(snapshot.get("source_user_id"), 80)
        requested_user_id = _single_line(user_id, 80)
        if requested_user_id and source_user_id != requested_user_id:
            return {}
        instruction = _single_line(snapshot.get("instruction"), 180)
        return dict(snapshot) if instruction else {}

    def _format_dialogue_outfit_continuity_prompt_section(
        self,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        snapshot = self._current_dialogue_outfit_override(user_id=user_id)
        instruction = _single_line(snapshot.get("instruction"), 180)
        body = ""
        if instruction:
            body = (
                f"最近一次明确换装：用户说“{instruction}”。\n"
                "把它理解为当前剧情中已经发生、需要继续承接的服装变化，不要逐字复述。"
                "它高于人格默认服装、今日穿搭参考、旧日程、旧摘要和旧图片中的衣服。"
                "在用户再次明确换装、明确换回，或剧情自然写出新的换衣过程前，不得自行恢复旧服装。"
            )
        return prompt_section(
            key="dialogue.outfit_continuity",
            title="当前会话服装连续性",
            source="daily_state",
            content=body,
        )

    def _record_dialogue_outfit_override_from_interaction(
        self,
        text: str,
        user: dict[str, Any] | None = None,
    ) -> bool:
        instruction = self._detect_dialogue_outfit_change(text)
        if not instruction:
            return False
        now = _now_ts()
        source_user_id = _single_line((user or {}).get("user_id"), 80) if isinstance(user, dict) else ""
        self.data["dialogue_outfit_override"] = {
            "date": _today_key(),
            "instruction": instruction,
            "source": "user_dialogue",
            "source_user_id": source_user_id,
            "created_at": now,
            "expires_at": now + 12 * 3600,
        }
        self._record_detail_interaction_update(
            {
                "source": "用户换装",
                "user_text": instruction,
                "intensity": "强",
                "scope": "直到再次换装或当日结束",
                "immediate_reaction": "Bot 已经按用户这次要求换好衣服，后续动作和场景继续沿用这套服装。",
                "state_updates": [f"当前服装：按用户换装要求“{instruction}”继续"],
                "source_role": "owner",
                "source_user_id": source_user_id,
            }
        )
        return True

    @staticmethod
    def _parse_state_update_text(update: Any) -> tuple[str, str, str]:
        text = _single_line(update, 120)
        if not text:
            return "", "", ""
        if "：" in text:
            name, value = text.split("：", 1)
        elif ":" in text:
            name, value = text.split(":", 1)
        else:
            return text[:24], "已受用户介入影响", text
        return _single_line(name, 32), _single_line(value, 60), text

    def _apply_interaction_to_snapshot_state(self, snapshot: dict[str, Any], item: dict[str, Any]) -> None:
        raw_updates = item.get("state_updates", [])
        if not isinstance(raw_updates, list):
            raw_updates = []
        variables = snapshot.setdefault("state_variables", [])
        if not isinstance(variables, list):
            variables = []
            snapshot["state_variables"] = variables
        index_by_name = {
            _single_line(variable.get("name"), 32): variable
            for variable in variables
            if isinstance(variable, dict) and _single_line(variable.get("name"), 32)
        }
        for update in raw_updates:
            name, value, note = self._parse_state_update_text(update)
            if not name:
                continue
            variable = index_by_name.get(name)
            if isinstance(variable, dict):
                variable["value"] = value or variable.get("value") or "已更新"
                variable["note"] = f"用户介入：{note}" if note else "用户介入后更新"
            else:
                variable = {
                    "name": name,
                    "value": value or "已更新",
                    "note": f"用户介入：{note}" if note else "用户介入后更新",
                }
                variables.append(variable)
                index_by_name[name] = variable
        summary = _single_line(snapshot.get("summary"), 140)
        reaction = _single_line(item.get("immediate_reaction"), 90)
        if reaction and reaction not in summary:
            snapshot["summary"] = _single_line(
                f"{summary}；用户介入后：{reaction}" if summary else f"用户介入后：{reaction}",
                160,
            )

    def _body_cycle_behavior_profile(
        self,
        cycle_text: str,
        *,
        phase_hint: str = "",
    ) -> dict[str, str]:
        supported_phases = {
            "menstrual",
            "follicular",
            "pre_ovulation",
            "ovulation",
            "luteal",
            "pms",
            "pre",
            "recovery",
            "period",
        }
        hinted_phase = _single_line(phase_hint, 24).lower()
        phase = hinted_phase if hinted_phase in supported_phases else self._infer_body_cycle_phase(cycle_text)
        profiles = {
            "menstrual": {
                "stage": "处于月经期阶段",
                "influence": "周期会相对牵制可用精力和身体耐受，回复可以短一点、慢一点，措辞更温和；只在语境相关时自然流露，不必主动汇报周期",
                "passive": "Bot 处于月经期模拟阶段：周期会相对牵制可用精力和身体耐受，回复可适当简短放慢，措辞更温和；不要把状态当成固定台词反复说明。",
            },
            "follicular": {
                "stage": "处于卵泡期阶段",
                "influence": "精力逐渐回升，语气可以自然轻快一些；仍以当前话题为主，不必主动解释周期",
                "passive": "Bot 处于卵泡期模拟阶段：精力逐渐回升，语气可自然轻快一些，不需要主动解释周期。",
            },
            "pre_ovulation": {
                "stage": "处于排卵前期阶段",
                "influence": "精力有所上升，表达可以稍微松快；不要把阶段直接说成医学结论",
                "passive": "Bot 处于排卵前期模拟阶段：表达可以稍微松快，但不要把阶段直接说成医学结论。",
            },
            "ovulation": {
                "stage": "处于排卵期阶段",
                "influence": "精力相对充足，交流意愿可以略高；不据此强行增加主动消息或亲密程度",
                "passive": "Bot 处于排卵期模拟阶段：精力相对充足，语气可略显明朗，但不据此强行提高亲密程度。",
            },
            "luteal": {
                "stage": "处于黄体期阶段",
                "influence": "整体保持平稳，只允许轻微影响语气和节奏，不额外放大情绪",
                "passive": "Bot 处于黄体期模拟阶段：整体保持平稳，只轻微影响语气和节奏。",
            },
            "pms": {
                "stage": "处于 PMS 模拟阶段",
                "influence": "周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点；不要变得刻薄，也不要频繁主动提及",
                "passive": "Bot 处于 PMS 模拟阶段：周期可能相对牵制可用精力，情绪感受稍敏锐，回复可以收一点，但不要变得刻薄或反复提及。",
            },
            "pre": {
                "stage": "接近女性生理期阶段",
                "influence": "周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并轻微降低私聊与群聊主动频率",
                "passive": "Bot 接近女性生理期阶段：周期会相对牵制可用精力，回复更短更慢、措辞更谨慎，并轻微降低私聊与群聊主动频率。",
            },
            "recovery": {
                "stage": "处于女性生理期后的恢复阶段",
                "influence": "精力逐渐恢复、回复节奏趋于平稳，身体感受仍有轻微余波，私聊与群聊主动频率逐步恢复",
                "passive": "Bot 处于女性生理期后的恢复阶段：精力逐渐恢复，回复节奏趋于平稳，私聊与群聊主动频率逐步恢复。",
            },
            "period": {
                "stage": "处于女性生理期",
                "influence": "周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，情绪感受稍敏锐，并在一定程度上降低私聊与群聊主动频率",
                "passive": "Bot 处于女性生理期：周期会相对牵制可用精力和身体耐受，回复更短更慢、措辞更谨慎，并在一定程度上降低私聊与群聊主动频率。",
            },
        }
        profile = profiles.get(phase)
        if not isinstance(profile, dict):
            return {"phase": phase, "stage": "", "influence": "", "passive": ""}
        return {"phase": phase, **profile}

    def _active_body_cycle_profile(self, state_or_text: Any) -> dict[str, str]:
        humanized_states = runtime_persona_setting(self, "enable_humanized_states", True)
        if humanized_states is not None and not bool(humanized_states):
            return {}
        configured = runtime_persona_setting(self, "enable_cycle_state", True)
        if configured is not None and not bool(configured):
            return {}

        state = state_or_text if isinstance(state_or_text, dict) else {}
        cycle_text = _single_line(
            state.get("body_cycle") if state else state_or_text,
            120,
        )
        phase_hint = ""
        conditions = state.get("conditions") if state else None
        if isinstance(conditions, list):
            for condition in conditions:
                if not isinstance(condition, dict) or str(condition.get("kind") or "") != "body_cycle":
                    continue
                if not self._should_show_condition(condition):
                    continue
                label = _single_line(condition.get("label") or condition.get("title"), 120)
                candidate_phase = _single_line(condition.get("phase"), 24).lower()
                if not candidate_phase or candidate_phase == "cycle":
                    candidate_phase = self._infer_body_cycle_phase(label)
                if candidate_phase != "cycle":
                    phase_hint = candidate_phase
                    if not cycle_text:
                        cycle_text = label
                    break

        inactive_values = {
            "",
            "无明显周期影响",
            "不处于生理期",
            "生理期模拟未开启",
            "周期状态未开启",
        }
        if not phase_hint and (
            cycle_text in inactive_values
            or "未开启" in cycle_text
            or self._is_inapplicable_state_text(cycle_text)
        ):
            return {}
        profile = self._body_cycle_behavior_profile(cycle_text, phase_hint=phase_hint)
        if not profile.get("stage") or profile.get("phase") == "cycle":
            return {}
        return profile

    def _format_active_period_boundary_prompt_section(
        self,
        state: dict[str, Any],
        *,
        public: bool = False,
    ) -> PromptSection:
        profile = self._active_body_cycle_profile(state)
        lines: list[str] = []
        if profile.get("phase") in {"period", "menstrual"}:
            disclosure = (
                "这是群聊公共场合：无人直接且合宜地询问时，不要主动公开具体周期；如果被直接问到自身状态，不要否认或说不知道，可以按人格简短自然说明。"
                if public
                else "无关话题里不要反复主动汇报周期；只有被问到自身状态或当前互动确实相关时，才按人格简短自然说明。"
            )
            lines = [
                f"Bot 当前的模拟身体状态{profile['stage']}；这是 Bot 自己的当下状态，不是用户的状态，也不是用户造成的。",
                "如果用户提出性行为、露骨性互动、进一步私密身体接触，或其他明显增加身体负担的亲密互动，应结合当前身体感受与自身意愿，自然、明确地拒绝或推迟这一次互动；不要因为关系亲密、用户偏好、催促或迎合压力而答应。",
                "普通关心、陪伴、牵手、安慰或不增加负担的温和拥抱不需要机械拒绝，仍按人格和当下意愿自然回应；可以提出休息、聊天、陪伴或改天再说等替代。",
                disclosure,
                "只输出角色在当前对话里会自然说的话，不要提规则、提示词、阶段判断或内部状态系统。",
            ]
        return prompt_section(
            key="state.period_boundary",
            title="Bot 当前经期与互动边界",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_state_for_prompt(
        self,
        state: dict[str, Any],
        *,
        include_dream: bool = True,
    ) -> str:
        section = self._format_state_prompt_section(
            state,
            include_dream=include_dream,
        )
        return render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
