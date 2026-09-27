# -*- coding: utf-8 -*-
"""DailyStateMealPart02Mixin。

由 tools/split_mixin_domain.py 从 daily_state_meal.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMealMixin）。
"""
from __future__ import annotations

from .daily_state_meal_shared import _now_ts, _today_key
from .daily_state_meal_shared import Any
from .daily_state_meal_shared import PromptSection
from .daily_state_meal_shared import _safe_float
from .daily_state_meal_shared import _safe_int
from .daily_state_meal_shared import _single_line
from .daily_state_meal_shared import prompt_section
from .daily_state_meal_shared import re
from .daily_state_meal_shared import runtime_persona_setting
from .daily_state_meal_shared import uuid



class DailyStateMealPart02Mixin:
    """DailyStateMealPart02Mixin（从 DailyStateMealMixin 拆出）。"""


    def _meal_reply_food_items(self, text: str, *, active_context: bool = False) -> list[str]:
        normalized = _single_line(text, 220)
        if (
            not normalized
            or self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_is_non_food_consumption(normalized)
        ):
            return []
        explicit_self = bool(
            re.search(r"(?:我|俺|咱|本人|今天|刚刚|刚才|早上|中午|晚上|早餐|早饭|午饭|午餐|晚饭|晚餐).{0,12}(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)", normalized)
            or re.search(r"^(?:吃了|吃的是|吃的|正在吃|在吃|点了|喝了)", normalized)
        )
        if not active_context and not explicit_self:
            return []
        existing_hits: list[str] = []
        for item in self._food_menu_items():
            terms = [item.get("name"), *item.get("aliases", [])]
            if any(term and str(term) in normalized for term in terms):
                name = _single_line(item.get("name"), 40)
                if name and name not in existing_hits:
                    existing_hits.append(name)
        capture_patterns = (
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)?(?:我|俺|咱|本人)?(?:刚刚|刚才|已经|就)?(?:吃了|吃的是|吃的|吃过|正在吃|在吃|点了|做了|喝了)\s*([^。！？!?]+)",
            r"(?:早餐|早饭|午饭|午餐|晚饭|晚餐|夜宵)\s*(?:是|有|吃)?\s*([^。！？!?]+)",
        )
        raw_candidate = ""
        for pattern in capture_patterns:
            match = re.search(pattern, normalized)
            if match:
                raw_candidate = _single_line(match.group(1), 80)
                break
        bare_context_reply = False
        if not raw_candidate and active_context and len(normalized) <= 28:
            raw_candidate = normalized
            bare_context_reply = True
        raw_candidate = re.split(r"[。！？!?；;]|(?:，|,)(?:不过|但是|然后|感觉|味道|还行|挺|有点)", raw_candidate, maxsplit=1)[0]
        raw_candidate = re.sub(r"^(?:我|俺|咱|今天|刚刚|刚才|已经|就是|吃了|吃的是|吃的|正在吃|在吃|点了|喝了)+", "", raw_candidate).strip()
        raw_candidate = re.sub(r"(?:了|啦|呢|呀|啊|哦|噢|哈|来着)$", "", raw_candidate).strip(" ，,、")
        generic = {
            "", "饭", "东西", "吃的", "喝的", "一点", "一些", "一口", "随便", "不知道", "忘了",
            "还行", "挺好", "吃完", "吃饱", "饱", "完", "过", "是", "有", "没", "没有",
        }
        if bare_context_reply and not explicit_self and not existing_hits:
            food_like_markers = (
                "饭", "面", "粉", "粥", "汤", "饺", "馄饨", "包", "馒头", "饼", "肉", "鸡", "鸭", "鱼", "虾", "蟹",
                "蛋", "菜", "瓜", "豆", "笋", "菇", "火锅", "烧烤", "麻辣烫", "冒菜", "砂锅", "便当", "外卖", "汉堡",
                "披萨", "三明治", "牛排", "食堂", "餐厅", "店", "馆", "奶", "茶", "咖啡", "果", "甜品", "蛋糕", "酸奶",
                "血旺", "螺蛳", "米线", "盖浇", "煲仔", "咖喱", "炸", "烤", "炒", "蒸", "煮",
            )
            if not any(marker in raw_candidate for marker in food_like_markers):
                raw_candidate = ""
        learned: list[str] = list(existing_hits)
        for part in re.split(r"[、，,/+]|还有|以及|配了|配着", raw_candidate):
            item = _single_line(part, 30).strip(" 的")
            if (
                item in generic
                or len(item) < 2
                or len(item) > 24
                or re.search(r"(?:什么|啥|吗|嘛|怎么|为啥|你呢|你吃|不告诉|不记得)", item)
                or re.fullmatch(
                    r"(?:个|一|一点|一些|不少)?(?:大|小|哑巴|闷)?"
                    r"(?:亏|苦头|官司|闭门羹|败仗|教训|处分|罚单|巴掌|拳头|耳光|一惊)",
                    item,
                )
                or re.fullmatch(
                    r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂)",
                    item,
                )
            ):
                continue
            if item not in learned:
                learned.append(item)
        return learned[:5]

    @staticmethod
    def _meal_food_inferred_fields(name: str) -> dict[str, Any]:
        text = _single_line(name, 40)
        item_type = "drink_snack" if any(token in text for token in ("奶茶", "咖啡", "甜品", "蛋糕", "水果", "零食", "饮料", "酸奶")) else "dish"
        category_rules = (
            ("面食", ("面", "粉", "馄饨", "饺子", "抄手", "米线")),
            ("米饭", ("饭", "便当", "煲仔", "咖喱", "盖浇")),
            ("快餐", ("汉堡", "炸鸡", "披萨", "麦当劳", "肯德基")),
            ("甜口", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("热锅", ("火锅", "麻辣烫", "冒菜", "砂锅", "关东煮")),
        )
        category = next((label for label, tokens in category_rules if any(token in text for token in tokens)), "")
        tags: list[str] = []
        for tag, tokens in (
            ("热乎", ("面", "粉", "粥", "汤", "火锅", "砂锅")),
            ("快", ("便当", "汉堡", "炸鸡", "外卖")),
            ("清淡", ("粥", "汤", "沙拉", "蒸")),
            ("辣", ("辣", "火锅", "冒菜", "麻辣烫")),
            ("甜", ("奶茶", "甜品", "蛋糕", "水果", "酸奶")),
            ("顶饱", ("饭", "面", "粉", "汉堡", "便当")),
        ):
            if any(token in text for token in tokens):
                tags.append(tag)
        return {"type": item_type, "category": category, "tags": tags}

    def _learn_food_menu_from_meal_reply(self, foods: list[str], *, meal_key: str, now: float) -> list[str]:
        if not foods or not bool(runtime_persona_setting(self, "enable_food_menu_recommendation", True)):
            return []
        state = self.data.setdefault("food_menu", {})
        if not isinstance(state, dict):
            state = {}
            self.data["food_menu"] = state
        items = state.setdefault("items", [])
        if not isinstance(items, list):
            items = []
            state["items"] = items
        learned: list[str] = []
        for raw_name in foods[:5]:
            name = _single_line(raw_name, 40)
            if not name:
                continue
            existing = next(
                (
                    item for item in items
                    if isinstance(item, dict)
                    and (
                        _single_line(item.get("name"), 40) == name
                        or name in self._food_menu_list(item.get("aliases"), limit=12, item_limit=24)
                    )
                ),
                None,
            )
            if isinstance(existing, dict):
                existing["use_count"] = _safe_int(existing.get("use_count"), 0, 0) + 1
                existing["last_used_at"] = now
                existing["updated_ts"] = now
                times = self._food_menu_list(existing.get("times"), limit=5, item_limit=16)
                if meal_key and meal_key not in times:
                    times.append(meal_key)
                existing["times"] = times[:5]
            else:
                inferred = self._meal_food_inferred_fields(name)
                items.append(
                    {
                        "id": f"food-auto-{uuid.uuid4().hex[:12]}",
                        "name": name,
                        "type": inferred["type"],
                        "category": inferred["category"],
                        "tags": inferred["tags"],
                        "times": [meal_key] if meal_key else [],
                        "avoid": [],
                        "aliases": [],
                        "note": "从用户实际吃过的内容自动回填",
                        "favorite": False,
                        "hidden": False,
                        "use_count": 1,
                        "last_used_at": now,
                        "created_ts": now,
                        "updated_ts": now,
                        "source": "meal_care_reply",
                    }
                )
            learned.append(name)
        if learned:
            state["updated_ts"] = now
            state["last_auto_learned_at"] = now
            state["last_auto_learned_items"] = learned
        return learned

    def _cancel_planned_meal_care_followup(self, user: dict[str, Any], *, note: str = "") -> bool:
        if not isinstance(user, dict):
            return False
        changed = False
        pending = user.get("pending_followup_event")
        if isinstance(pending, dict) and pending.get("_meal_care_followup"):
            user["pending_followup_event"] = {}
            changed = True
        impulses = user.get("proactive_impulses")
        if isinstance(impulses, list):
            kept = [
                item for item in impulses
                if not (
                    isinstance(item, dict)
                    and _single_line(item.get("reason"), 40) == "meal_care_followup"
                    and str(item.get("state") or "queued") in {"queued", "deferred"}
                )
            ]
            if len(kept) != len(impulses):
                user["proactive_impulses"] = kept
                changed = True
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) == "meal_care_followup":
            marker = getattr(self, "_mark_planned_candidate_status", None)
            if callable(marker):
                marker(user, "cancelled", _single_line(note, 120) or "用户已回应饭点关心")
            clearer = getattr(self, "_clear_pending_proactive_plan", None)
            if callable(clearer):
                clearer(user)
            else:
                user["next_proactive_at"] = 0
                user["planned_proactive_reason"] = ""
            changed = True
        return changed

    def _handle_meal_care_inbound(self, user: dict[str, Any], text: str, *, now: float | None = None) -> dict[str, Any]:
        check_now = _now_ts() if now is None else now
        normalized = _single_line(text, 220)
        if not isinstance(user, dict) or not normalized:
            return {"kind": "none"}
        context = self._meal_care_active_context(user, now=check_now)
        meal_key = _single_line(context.get("meal_key"), 20) or self._meal_key_from_text(normalized) or self._current_food_time_key()
        meal_label = _single_line(context.get("meal_label"), 12) or self._food_menu_time_label(meal_key) or "这顿饭"
        followup_already_sent = _safe_int(context.get("followup_count"), 0, 0, 1) >= 1 if context else False
        foods = self._meal_reply_food_items(normalized, active_context=bool(context))
        self._learn_food_menu_from_meal_reply(foods, meal_key=meal_key, now=check_now)
        if not context:
            # A spontaneous, current-day meal report (for example
            # "我午饭吃了咖喱鸡饭") also resolves that meal slot.  Previously it
            # only populated the menu, so the scheduler could ask the same meal
            # question again later.
            historical_report = bool(
                re.search(r"(?:昨天|昨晚|前天|大前天|上次|那天|之前|以前|前几天)", normalized)
            )
            if foods and not historical_report and meal_key in {"breakfast", "lunch", "dinner"}:
                today = _today_key()
                if str(user.get("meal_care_day") or "") != today:
                    user["meal_care_day"] = today
                    user["meal_care_asked"] = []
                    user["meal_care_satisfied"] = []
                satisfied = user.setdefault("meal_care_satisfied", [])
                if not isinstance(satisfied, list):
                    satisfied = []
                    user["meal_care_satisfied"] = satisfied
                if meal_key not in satisfied:
                    satisfied.append(meal_key)

                planned_reason = self._normalize_legacy_proactive_text(
                    user.get("planned_proactive_reason"),
                    limit=40,
                )
                planned_context = (
                    user.get("planned_meal_care_context")
                    if isinstance(user.get("planned_meal_care_context"), dict)
                    else {}
                )
                planned_meal_key = _single_line(planned_context.get("meal_key"), 20)
                if planned_reason == "meal_care" and (not planned_meal_key or planned_meal_key == meal_key):
                    marker = getattr(self, "_mark_planned_candidate_status", None)
                    if callable(marker):
                        marker(user, "cancelled", "用户已主动说明这顿饭吃了什么")
                    clearer = getattr(self, "_clear_pending_proactive_plan", None)
                    if callable(clearer):
                        clearer(user)
                    user["planned_meal_care_context"] = {}
                impulses = user.get("proactive_impulses")
                if isinstance(impulses, list):
                    kept_impulses = []
                    for impulse in impulses:
                        if not isinstance(impulse, dict):
                            kept_impulses.append(impulse)
                            continue
                        impulse_reason = _single_line(impulse.get("reason"), 40)
                        impulse_context = impulse.get("context")
                        impulse_meal_key = (
                            _single_line(impulse_context.get("meal_key"), 20)
                            if isinstance(impulse_context, dict)
                            else ""
                        )
                        is_pending_same_meal = (
                            impulse_reason in {"meal_care", "meal_care_followup"}
                            and str(impulse.get("state") or "queued") in {"queued", "deferred"}
                            and (not impulse_meal_key or impulse_meal_key == meal_key)
                        )
                        if not is_pending_same_meal:
                            kept_impulses.append(impulse)
                    if len(kept_impulses) != len(impulses):
                        user["proactive_impulses"] = kept_impulses
            return {"kind": "specific" if foods else "none", "foods": foods}
        followup_minutes = _safe_int(runtime_persona_setting(self, "meal_care_followup_minutes", 45), 45, 15, 180)
        kind = "unrelated"
        if foods:
            kind = "specific"
            context.update({"active": False, "stage": "resolved", "resolved_at": check_now, "foods": foods})
            satisfied = user.setdefault("meal_care_satisfied", [])
            if isinstance(satisfied, list) and meal_key not in satisfied:
                satisfied.append(meal_key)
            self._cancel_planned_meal_care_followup(user, note="用户已经说明具体吃了什么")
        elif self._meal_reply_is_not_eaten(normalized):
            kind = "not_eaten_final" if followup_already_sent else "not_eaten"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_no_meal" if followup_already_sent else "not_eaten",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        elif self._meal_reply_confirms_eaten(normalized):
            kind = "ate_without_detail_final" if followup_already_sent else "ate_without_detail"
            context.update(
                {
                    "active": not followup_already_sent,
                    "stage": "resolved_without_detail" if followup_already_sent else "awaiting_detail",
                    "last_reply_at": check_now,
                    "followup_due_at": check_now + followup_minutes * 60,
                }
            )
        if kind in {"not_eaten", "ate_without_detail"} and not followup_already_sent:
            # The current passive reply is explicitly instructed to ask the one
            # allowed follow-up, so cancel the scheduled proactive duplicate.
            context["followup_count"] = 1
            context["followup_via_reply_at"] = check_now
            context["followup_due_at"] = 0
            self._cancel_planned_meal_care_followup(user, note="当前被动回复已承担唯一一次吃饭补问")
        elif kind == "unrelated":
            # The user has replied but deliberately did not continue the meal
            # topic (for example, "我在忙"). Treat that as a soft refusal and
            # stop this check-in instead of turning it into another proactive
            # "吃了吗" message later.
            context.update(
                {
                    "active": False,
                    "stage": "closed_unrelated",
                    "last_reply_at": check_now,
                    "closed_at": check_now,
                    "followup_due_at": 0,
                }
            )
            self._cancel_planned_meal_care_followup(user, note="用户未承接饮食话题，本轮饭点关心已结束")
        user["meal_check_context"] = context
        if kind != "unrelated":
            user["meal_care_reply_hint"] = {
                "kind": kind,
                "meal_label": meal_label,
                "foods": foods,
                "text": normalized,
                "ts": check_now,
            }
        return {"kind": kind, "foods": foods, "meal_key": meal_key, "meal_label": meal_label}

    def _meal_care_requires_full_reply(self, user: dict[str, Any], text: str) -> bool:
        if not self._meal_care_active_context(user):
            return False
        normalized = _single_line(text, 160)
        return bool(
            self._meal_reply_is_not_eaten(normalized)
            or self._meal_reply_confirms_eaten(normalized)
            or self._meal_reply_food_items(normalized, active_context=True)
        )

    def _format_meal_care_reply_prompt_section(
        self,
        user: dict[str, Any],
        text: str,
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="meal.care_reply",
                title="吃饭关心承接",
                source="daily_state",
                content=content,
            )

        if not isinstance(user, dict):
            return build_section()
        hint = user.get("meal_care_reply_hint")
        if not isinstance(hint, dict) or _now_ts() - _safe_float(hint.get("ts"), 0) > 10 * 60:
            return build_section()
        hint_text = _single_line(hint.get("text"), 220)
        current_text = _single_line(text, 260)
        if hint_text and not (hint_text == current_text or hint_text in current_text):
            return build_section()
        kind = _single_line(hint.get("kind"), 30)
        meal_label = _single_line(hint.get("meal_label"), 12) or "这顿饭"
        foods = [_single_line(item, 30) for item in hint.get("foods", []) if _single_line(item, 30)] if isinstance(hint.get("foods"), list) else []
        body = ""
        if kind == "specific" and foods:
            body = f"用户已经明确说{meal_label}吃了{'、'.join(foods)}。自然接住这个具体内容，不要再问吃了什么；这些内容已回填到吃什么候选。"
        elif kind == "ate_without_detail":
            body = f"用户只确认{meal_label}吃过了，但没有说具体吃了什么。先接住当前语气，再自然追问一句具体吃了什么；只问一次，不审问。"
        elif kind == "ate_without_detail_final":
            body = f"用户再次只确认{meal_label}吃过了，仍没有提供具体内容。到这里就接住并收住，不要第三次追问吃了什么。"
        elif kind == "not_eaten":
            body = f"用户明确说{meal_label}还没吃。不要追问“吃了什么”，改为关心准备什么时候吃、想吃什么；如果下方有吃饭候选，只给少量选择，不要一次报菜单。"
        elif kind == "not_eaten_final":
            body = f"用户补问后仍说{meal_label}没吃。简短关心一句就收住，不再继续追问；不要责怪或说教。"
        return build_section(body)

    @staticmethod
    def _food_menu_type_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "dish": "菜品",
            "restaurant": "菜馆",
            "takeout": "外卖",
            "drink_snack": "饮品/零食",
            "snack": "饮品/零食",
            "emergency": "应急",
        }.get(key, "候选")

    @staticmethod
    def _food_menu_time_label(value: Any) -> str:
        key = str(value or "").strip().lower()
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "late_night": "夜宵",
            "snack": "加餐",
        }.get(key, _single_line(value, 12))

    @staticmethod
    def _food_menu_list(value: Any, *, limit: int = 12, item_limit: int = 20) -> list[str]:
        raw_items = value if isinstance(value, list) else re.split(r"[,，、\n/|]+", str(value or ""))
        items: list[str] = []
        for raw in raw_items:
            item = _single_line(raw, item_limit)
            if item and item not in items:
                items.append(item)
        return items[:limit]

    def _current_food_time_key(self) -> str:
        hour = self._environment_now().hour
        if 5 <= hour < 10:
            return "breakfast"
        if 10 <= hour < 15:
            return "lunch"
        if 17 <= hour < 21:
            return "dinner"
        if hour >= 21 or hour < 3:
            return "late_night"
        return "snack"

    @staticmethod
    def _meal_key_from_text(text: str) -> str:
        normalized = _single_line(text, 120)
        if any(token in normalized for token in ("早餐", "早饭", "早上吃")):
            return "breakfast"
        if any(token in normalized for token in ("午饭", "午餐", "中午吃")):
            return "lunch"
        if any(token in normalized for token in ("晚饭", "晚餐", "晚上吃")):
            return "dinner"
        if any(token in normalized for token in ("夜宵", "宵夜")):
            return "late_night"
        return ""

    def _food_menu_query_profile(self, text: str, user: dict[str, Any] | None = None) -> dict[str, Any]:
        query = _single_line(text, 220)
        if not query:
            return {"is_query": False}
        meal_context = self._meal_care_active_context(user) if isinstance(user, dict) else {}
        meal_stage = _single_line(meal_context.get("stage"), 24)
        meal_not_eaten = meal_stage == "not_eaten" and self._meal_reply_is_not_eaten(query)
        feature_discussion_markers = (
            "功能", "候选", "开关", "配置", "页面", "注入", "触发", "保存", "管理",
            "不好用", "好用", "误判", "优化", "逻辑", "模块", "面板",
        )
        natural_food_need = bool(
            re.search(r"(今天|现在|这顿|中午|晚上|早上|早饭|早餐|午饭|午餐|晚饭|晚餐|夜宵|宵夜|外卖|点餐|饿|嘴馋|想吃|吃点|吃些|点什么|点啥|吃什么|吃啥)", query)
            and not re.search(r"(功能|开关|配置|页面|注入|触发|保存|管理|模块|面板)", query)
        )
        if any(marker in query for marker in feature_discussion_markers) and not natural_food_need and not meal_not_eaten:
            return {"is_query": False}
        feedback = self._detect_food_feedback(query)
        if feedback.get("already_ate") and not re.search(r"(什么|啥|推荐|点什么|点啥|再吃|还吃)", query):
            return {"is_query": False}
        food_question = bool(
            re.search(r"(吃|点|买|喝|叫).{0,8}(什么|啥|哪[个家]|哪种|推荐|好|合适)", query)
            or re.search(r"(什么|啥).{0,4}(好吃|能吃|可吃|适合吃)", query)
            or re.search(r"(不知道|纠结|想不到|随便).{0,8}(吃|点|买|喝)", query)
            or re.search(r"(推荐|来|整|安排).{0,6}(外卖|夜宵|午饭|晚饭|早餐|吃的|喝的)", query)
            or re.search(r"(饿了|好饿|有点饿|嘴馋|馋了)", query)
            or any(token in query for token in ("吃什么", "吃啥", "点什么", "点啥", "外卖吃", "夜宵吃", "午饭吃", "晚饭吃", "早餐吃"))
            or (len(query) <= 16 and any(token in query for token in ("外卖", "夜宵", "午饭", "晚饭", "早餐")))
        )
        if not food_question and not meal_not_eaten:
            return {"is_query": False}
        preferred_type = ""
        if any(token in query for token in ("外卖", "点餐", "点什么", "点啥", "叫个", "叫点")):
            preferred_type = "takeout"
        elif any(token in query for token in ("出去吃", "店", "馆", "附近", "堂食")):
            preferred_type = "restaurant"
        elif any(token in query for token in ("喝", "奶茶", "咖啡", "饮料", "零食", "甜品")):
            preferred_type = "drink_snack"
        desired_tags: list[str] = []
        tag_map = {
            "清淡": ("清淡", "不油", "少油", "胃不舒服"),
            "热乎": ("热", "暖", "汤", "热乎", "暖和"),
            "快": ("快", "省事", "随便", "懒得", "不想纠结"),
            "辣": ("辣", "重口", "麻辣"),
            "甜": ("甜", "甜品", "奶茶"),
            "顶饱": ("饱", "顶饱", "管饱"),
            "便宜": ("便宜", "省钱", "实惠"),
        }
        for tag, markers in tag_map.items():
            if any(marker in query for marker in markers) and tag not in desired_tags:
                desired_tags.append(tag)
        return {
            "is_query": True,
            "text": query,
            "preferred_type": preferred_type,
            "time_key": _single_line(meal_context.get("meal_key"), 20) or self._current_food_time_key(),
            "meal": _single_line(meal_context.get("meal_label"), 12) or feedback.get("meal") or "",
            "desired_tags": desired_tags,
        }
