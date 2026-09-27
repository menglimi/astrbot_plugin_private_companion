# -*- coding: utf-8 -*-
"""SceneContextPart04Mixin。

由 tools/split_mixin_domain.py 从 scene_context.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 154 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 SceneContextMixin）。
"""
from __future__ import annotations

from .conversation_prompt_section import PromptRenderMode, PromptSection, prompt_section, render_prompt_sections
from .helpers import _single_line
from typing import Any



class SceneContextPart04Mixin:
    """SceneContextPart04Mixin（从 SceneContextMixin 拆出）。"""


    def _mobile_user_proactive_scene(
        self,
        user: dict[str, Any] | None,
        *,
        now: float | None = None,
        include_map: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded semantic location signal for proactive planning.

        ``include_map`` additionally reports user-marked place names so callers
        that need the cognitive-map background reuse this pass instead of
        querying the reality bridge and map observer a second time.
        """
        current_user = user if isinstance(user, dict) else {}
        user_id = _single_line(current_user.get("user_id") or current_user.get("id"), 80)
        getter = getattr(self, "_reality_mobile_context", None)
        if not user_id or not callable(getter):
            return {}
        try:
            mobile_context = getter(user_id)
        except Exception:
            return {}
        if not isinstance(mobile_context, dict):
            return {}
        location = mobile_context.get("location") if isinstance(mobile_context.get("location"), dict) else {}
        cognitive_map = self._mobile_cognitive_map(user_id, location, include_transition=True)
        scene = self._mobile_presence_state(mobile_context, cognitive_map, now=now)
        if not scene.get("available"):
            return {}
        if include_map:
            scene["known_places"] = self._known_places_from_map(cognitive_map)
        return scene

    def _mobile_cognitive_map(
        self,
        user_id: str,
        location: dict[str, Any],
        *,
        include_transition: bool = False,
    ) -> dict[str, Any]:
        map_observer = getattr(self, "_observe_mobile_place_context", None)
        if not callable(map_observer):
            return {}
        try:
            try:
                candidate = map_observer(user_id, location, include_transition=include_transition)
            except TypeError:
                candidate = map_observer(user_id, location)
            return candidate if isinstance(candidate, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _known_places_from_map(cognitive_map: dict[str, Any]) -> list[Any]:
        return cognitive_map.get("known_places") if isinstance(cognitive_map.get("known_places"), list) else []

    def _format_mobile_user_location_context_for_proactive_prompt_section(
        self,
        user: dict[str, Any] | None,
    ) -> PromptSection | None:
        """Format location as a low-pressure scene hint for proactive messages.

        Proactive prompts should not receive the private-dialogue coordinate detail.  They
        only need a coarse, authorized signal that can make a topic or timing feel natural.
        """
        scene = self._mobile_user_proactive_scene(user, include_map=True)
        if not scene:
            return None
        facts: list[str] = []
        area_label = _single_line(scene.get("area_label"), 100)
        if area_label:
            facts.append(f"城市/城区背景：{area_label}（仅作粗粒度环境线索，不代表精确地址）")
        presence_state = _single_line(scene.get("presence_state"), 32)
        if presence_state == "at_place":
            place_name = _single_line(scene.get("place_name"), 40)
            kind = _single_line(scene.get("place_kind"), 24)
            kind_label = {"home": "家", "work": "工作地点", "custom": "自定义地点"}.get(kind, "已标记地点")
            facts.append(f"用户当前位于已标记地点“{place_name}”（{kind_label}）范围内")
            if bool(scene.get("recent_arrival")):
                facts.append("这是最近一次进入该地点后的短时间窗口，可把它理解为刚到达后的生活节点")
        elif presence_state == "departing":
            place_name = _single_line(scene.get("place_name"), 40)
            facts.append(f"用户正在离开已标记地点“{place_name}”的识别范围，但离开事件尚未确认")
        elif presence_state == "arriving":
            place_name = _single_line(scene.get("place_name"), 40)
            facts.append(f"用户正在进入已标记地点“{place_name}”的识别范围，但到达事件尚未确认")
        elif presence_state == "near_place":
            place_name = _single_line(scene.get("place_name"), 40)
            facts.append(f"用户接近已标记地点“{place_name}”的边界，当前不能断言在地点内或已经离开")
        elif presence_state == "in_transit":
            place_name = _single_line(scene.get("place_name"), 40)
            if bool(scene.get("recent_departure")) and place_name:
                facts.append(f"用户刚离开已标记地点“{place_name}”，目前处于范围外，可作为在路上的生活节点")
            else:
                facts.append("用户当前处于已标记地点范围外且位置仍在变化，只可理解为在路上，目的地未知")
        elif presence_state == "away":
            facts.append("未命中已标记地点，用户当前处于已标记地点范围外，不能继续沿用在家或在公司的描述")
        elif presence_state == "unknown":
            facts.append("用户已授权位置感知，但当前没有足够地点信息判断是在固定地点还是路上")
        else:
            facts.append("当前仅有短期设备可达性信息，没有足够位置证据判断用户所在场景")

        # A few user-created place names can help distinguish home/work context, but
        # routes and coordinates are intentionally excluded from proactive prompts.
        known_places = scene.get("known_places") if isinstance(scene.get("known_places"), list) else []
        known_names = [
            _single_line(item.get("name"), 32)
            for item in known_places[:4]
            if isinstance(item, dict) and _single_line(item.get("name"), 32)
        ]
        if known_names and presence_state != "at_place":
            facts.append("用户主动标记过的地点背景包括：" + "、".join(known_names))
        elif known_names:
            # Keep the map signal bounded without copying its route history into the prompt.
            facts.append("地点认知背景：已保存少量用户主动标记地点，不能据此推断当前路线")
        weather_getter = getattr(self, "_weather_summary_text", None)
        try:
            weather = _single_line(
                weather_getter(self.data.get("daily_weather", {})) if callable(weather_getter) else "",
                120,
            )
        except Exception:
            weather = ""
        weather_risk = self._mobile_location_weather_is_safety_relevant(weather)
        if weather and bool(scene.get("recent_departure")) and weather_risk:
            facts.append("用户刚离开已标记地点，且当前有风雨风险；可以把主动提醒落在路上留意安全，但不要夸大风险或假定用户正在室外")
        elif weather and bool(scene.get("matched")) and _single_line(scene.get("place_kind"), 24) == "work" and any(
            token in weather for token in ("雨", "阵雨", "雷雨", "暴雨")
        ):
            facts.append("若本轮涉及雨天出行，优先把时机理解为下班或回家前；没有更近事实时，不必写成用户此刻正要出门")
        if bool(scene.get("recent_arrival")) and _single_line(scene.get("place_kind"), 24) == "home":
            facts.append("可优先把主动话题落在刚到家后的问候或收尾，避免继续使用上班、在路上等通勤措辞")
        app_state = _single_line(scene.get("device_app_state"), 24)
        battery = scene.get("battery_percent")
        if app_state == "foreground":
            facts.append("陪伴终端当前在前台，可把消息写得像自然承接，但这不等于用户正在持续注视屏幕")
        elif app_state == "background":
            facts.append("陪伴终端当前在后台；这不代表用户离线，也不要主动提及后台状态")
        if isinstance(battery, int) and battery <= 15 and not bool(scene.get("charging")):
            facts.append("设备电量偏低；若要主动表达应尽量简短，不邀请长通话，也不要把电量本身写成话题")
        return prompt_section(
            key="proactive.mobile_location",
            title="主动场景位置线索",
            source="scene_context",
            content=(
                "；".join(facts)
                + "\n这是用户授权的弱场景证据，只用于调整主动话题、时机和语气（如通勤、到家或工作间隙）。"
                "不要主动复述地点、坐标、轨迹或设备状态，不要从这些信号推断用户正在做的具体动作，也不要把位置本身硬写成主动话题，不要把感知本身硬写成主动话题。"
            ),
        )

    def _format_mobile_user_location_context_for_proactive(self, user: dict[str, Any] | None) -> str:
        section = self._format_mobile_user_location_context_for_proactive_prompt_section(user)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )
