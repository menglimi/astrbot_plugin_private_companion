# -*- coding: utf-8 -*-
"""DailyStateMealPart03Mixin。

由 tools/split_mixin_domain.py 从 daily_state_meal.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 162 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMealMixin）。
"""
from __future__ import annotations

from .daily_state_meal_shared import _now_ts
from .daily_state_meal_shared import Any
from .daily_state_meal_shared import PromptSection
from .daily_state_meal_shared import _safe_float
from .daily_state_meal_shared import _safe_int
from .daily_state_meal_shared import _single_line
from .daily_state_meal_shared import prompt_section



class DailyStateMealPart03Mixin:
    """DailyStateMealPart03Mixin（从 DailyStateMealMixin 拆出）。"""


    def _food_menu_items(self) -> list[dict[str, Any]]:
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            name = _single_line(raw.get("name"), 40)
            if not name:
                continue
            item = dict(raw)
            item["name"] = name
            item["type"] = _single_line(item.get("type"), 20) or "dish"
            item["category"] = _single_line(item.get("category"), 24)
            item["tags"] = self._food_menu_list(item.get("tags"), limit=10, item_limit=16)
            item["times"] = self._food_menu_list(item.get("times"), limit=5, item_limit=16)
            item["avoid"] = self._food_menu_list(item.get("avoid"), limit=8, item_limit=24)
            item["aliases"] = self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)
            item["note"] = _single_line(item.get("note"), 80)
            item["favorite"] = bool(item.get("favorite"))
            item["hidden"] = bool(item.get("hidden"))
            normalized.append(item)
        return normalized

    def _score_food_menu_item(self, item: dict[str, Any], profile: dict[str, Any]) -> float:
        query = str(profile.get("text") or "")
        if item.get("hidden"):
            return -999.0
        for token in item.get("avoid", []):
            if token and token in query:
                return -999.0
        score = 1.0
        if item.get("favorite"):
            score += 1.2
        preferred_type = str(profile.get("preferred_type") or "")
        if preferred_type and str(item.get("type") or "") == preferred_type:
            score += 2.4
        times = item.get("times") if isinstance(item.get("times"), list) else []
        if times:
            score += 1.5 if profile.get("time_key") in times else -0.8
        desired_tags = profile.get("desired_tags") if isinstance(profile.get("desired_tags"), list) else []
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        score += sum(
            0.9
            for desired in desired_tags
            if any(desired == tag or desired in tag or tag in desired for tag in tags)
        )
        category = str(item.get("category") or "")
        searchable = [item.get("name"), category, item.get("note"), *item.get("aliases", []), *tags]
        if any(part and str(part) in query for part in searchable):
            score += 2.8
        last = _safe_float(item.get("last_recommended_at"), 0, 0)
        if last > 0:
            age_hours = max(0.0, (_now_ts() - last) / 3600)
            if age_hours < 8:
                score -= 1.4
            elif age_hours < 36:
                score -= 0.5
        score += min(0.8, _safe_int(item.get("use_count"), 0, 0) * 0.04)
        return score

    def _mark_food_menu_items_recommended(self, candidates: list[dict[str, Any]]) -> None:
        ids = {
            _single_line(item.get("id"), 48)
            for item in candidates
            if isinstance(item, dict) and _single_line(item.get("id"), 48)
        }
        if not ids:
            return
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return
        now = _now_ts()
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if _single_line(item.get("id"), 48) in ids:
                item["last_recommended_at"] = now
                item["updated_ts"] = now
                changed = True
        if changed:
            state["updated_ts"] = now
            self.data["food_menu"] = state
            self._save_data_sync(sections={"food_menu"})

    def _food_menu_candidates_for_prompt(self, text: str, *, limit: int = 3, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self._food_menu_items():
            score = self._score_food_menu_item(item, profile)
            if score > -100:
                scored.append((score, item))
        scored.sort(key=lambda pair: (pair[0], bool(pair[1].get("favorite")), _safe_int(pair[1].get("use_count"), 0, 0)), reverse=True)
        return [item for _, item in scored[: max(1, min(5, limit))]]

    def _format_food_menu_reply_prompt_section(
        self,
        text: str,
        *,
        limit: int = 3,
        user: dict[str, Any] | None = None,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="meal.food_candidates",
                title="吃饭候选",
                source="daily_state",
                content=content,
            )

        profile = self._food_menu_query_profile(text, user=user)
        if not profile.get("is_query"):
            return build_section()
        candidates = self._food_menu_candidates_for_prompt(text, limit=limit, user=user)
        if not candidates:
            return build_section()
        self._mark_food_menu_items_recommended(candidates)
        lines: list[str] = []
        for item in candidates:
            parts = [item.get("name")]
            label = self._food_menu_type_label(item.get("type"))
            category = _single_line(item.get("category"), 18)
            if category:
                label = f"{label}/{category}"
            meta = [label]
            times = [self._food_menu_time_label(value) for value in item.get("times", []) if self._food_menu_time_label(value)]
            if times:
                meta.append("适合" + "、".join(times[:3]))
            tags = item.get("tags", [])[:4]
            if tags:
                meta.append("偏" + "、".join(tags))
            note = _single_line(item.get("note"), 54)
            detail = "，".join(meta)
            line = f"{parts[0]}（{detail}）"
            if note:
                line += f"：{note}"
            lines.append(line)
        meal = _single_line(profile.get("meal"), 12) or self._food_menu_time_label(profile.get("time_key")) or "这顿"
        body = f"这轮用户在问{meal}吃什么。可参考：" + "；".join(lines) + "。"
        return build_section(body)

    def _mark_food_menu_item_used_from_text(self, text: str) -> list[str]:
        query = _single_line(text, 220)
        if not query:
            return []
        state = self.data.get("food_menu") if isinstance(self.data.get("food_menu"), dict) else {}
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if not items:
            return []
        now = _now_ts()
        matched: list[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            terms = [item.get("name"), *self._food_menu_list(item.get("aliases"), limit=10, item_limit=24)]
            if any(term and str(term) in query for term in terms):
                item["use_count"] = _safe_int(item.get("use_count"), 0, 0) + 1
                item["last_used_at"] = now
                matched.append(_single_line(item.get("name"), 40))
        if matched:
            state["updated_ts"] = now
            self.data["food_menu"] = state
        return matched[:5]
