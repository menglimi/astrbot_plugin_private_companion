# -*- coding: utf-8 -*-
"""GroupWakeupPart04Mixin。

由 tools/split_mixin_domain.py 从 group_wakeup.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 49 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupWakeupMixin）。
"""
from __future__ import annotations
from .group_wakeup_shared import Any
from .group_wakeup_shared import _now_ts
from .group_wakeup_shared import _single_line
from .group_wakeup_shared import _today_key



class GroupWakeupPart04Mixin:
    """GroupWakeupPart04Mixin（从 GroupWakeupMixin 拆出）。"""


    def _scene_note_text(self, scene: dict[str, Any]) -> str:
        target = str(scene.get("talking_to") or "group")
        trigger = str(scene.get("trigger") or "")
        if target == "bot":
            if trigger.startswith("group_wakeup_"):
                return "本轮由群聊唤醒触发：群里提到 Bot 或相关话题。"
            return "当前消息在和 Bot 对话。"
        if target != "group":
            return f"当前消息主要在和{self._scene_talking_to_text(scene)}说话。"
        if trigger == "at_all":
            return "当前消息 @ 全体，包含 Bot。"
        return "当前消息主要面向整个群。"

    def _record_group_wakeup_state_adjustment(
        self,
        *,
        scene: dict[str, Any],
        text: str,
        state_note: str,
        updates: list[str],
        intensity: str = "轻",
        carry_rule: str = "群聊唤醒只作为状态和语气背景承接,不要在回复里暴露关键词触发、概率或内部判断。",
    ) -> None:
        note = _single_line(state_note, 180)
        if not note:
            return
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        now = _now_ts()
        trigger = _single_line(scene.get("trigger"), 40)
        word = _single_line(scene.get("wakeup_word"), 60)
        raw.append(
            {
                "date": _today_key(),
                "source": "群聊唤醒",
                "note": note,
                "immediate_reaction": _single_line(f"群聊里{('提到“' + word + '”') if word else '出现了可接话契机'},她会按当前状态自然反应。", 140),
                "state_updates": updates[:5],
                "user_text": _single_line(text, 120),
                "intensity": intensity,
                "scope": "当前段和短时间群聊回复",
                "carry_rule": carry_rule,
                "created_at": now,
                "expires_at": now + (2 * 3600 if intensity == "轻" else 4 * 3600),
                "trigger": trigger,
            }
        )
        del raw[:-16]
