# -*- coding: utf-8 -*-
"""DailyStateMealPart01Mixin。

由 tools/split_mixin_domain.py 从 daily_state_meal.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 487 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateMealMixin）。
"""
from __future__ import annotations

from .daily_state_meal_shared import _now_ts, _today_key, logger
from .daily_state_meal_shared import Any
from .daily_state_meal_shared import _safe_float
from .daily_state_meal_shared import _safe_int
from .daily_state_meal_shared import _single_line
from .daily_state_meal_shared import datetime
from .daily_state_meal_shared import hashlib
from .daily_state_meal_shared import random
from .daily_state_meal_shared import re



class DailyStateMealPart01Mixin:
    """DailyStateMealPart01Mixin（从 DailyStateMealMixin 拆出）。"""


    def _meal_log_date_key(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).strftime("%Y-%m-%d")
        except Exception:
            return _today_key()

    def _meal_log_iso_time(self, ts: float | None = None) -> str:
        try:
            return self._environment_fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")
        except Exception:
            return datetime.fromtimestamp(ts or _now_ts()).isoformat(timespec="seconds")

    def _extract_self_meal_events_from_text(
        self,
        text: Any,
        *,
        default_meal: str = "",
        source: str = "",
    ) -> list[dict[str, Any]]:
        raw = _single_line(text, 260)
        if not raw:
            return []
        if not any(token in raw for token in ("吃", "喝", "点了", "煮了", "做了", "买了", "饭", "餐", "夜宵", "便当", "外卖")):
            return []
        if re.search(r"(想吃|想喝|要不要|吃什么|吃啥|没吃|还没吃|准备吃|等会吃|待会吃|可能吃|可以吃|推荐|建议)", raw):
            return []
        action_match = re.search(
            r"(?:我|她|星缘)?(?:刚刚|刚|已经|中午|晚上|早上|午后|夜里|下午|早餐|午餐|晚餐|夜宵|这顿)?"
            r"(?:吃了|吃过|吃完|喝了|点了|煮了|做了|买了|啃了|咬了|尝了|解决了)"
            r"([^，。；、\n]{1,36})",
            raw,
        )
        meal_match = re.search(r"(早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|加餐|下午茶)", raw)
        meal = _single_line((meal_match.group(1) if meal_match else "") or default_meal, 20)
        food = ""
        if action_match:
            food = _single_line(action_match.group(1), 40)
            food = re.sub(r"^(点|些|个|一点|一点儿|一份|一碗|一杯|一口|点儿)", "", food).strip()
            food = re.sub(r"(之后|以后|然后|顺手|才发现|的时候).*$", "", food).strip()
        if not food:
            simple = re.search(r"(?:早餐|早饭|午餐|午饭|晚餐|晚饭|夜宵|下午茶)[^，。；、\n]{0,8}(?:是|吃|喝|点)([^，。；、\n]{1,32})", raw)
            if simple:
                food = _single_line(simple.group(1), 40)
        if not food or food in {"饭", "东西", "一点", "点东西"}:
            return []
        return [
            {
                "meal": meal or "加餐",
                "food": food,
                "source": _single_line(source, 40),
                "evidence": raw,
            }
        ]

    def _collect_self_meal_events_from_detail(
        self,
        *,
        segment: dict[str, Any],
        plan: dict[str, Any],
        detail: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(detail, dict):
            return []
        default_meal = ""
        item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
        schedule_text = " ".join(
            _single_line(part, 120)
            for part in (
                item.get("time") if isinstance(item, dict) else "",
                item.get("activity") if isinstance(item, dict) else "",
                detail.get("summary"),
            )
            if _single_line(part, 120)
        )
        if any(token in schedule_text for token in ("早餐", "早饭")):
            default_meal = "早餐"
        elif any(token in schedule_text for token in ("午餐", "午饭", "中午")):
            default_meal = "午餐"
        elif any(token in schedule_text for token in ("晚餐", "晚饭", "晚上")):
            default_meal = "晚餐"
        elif "夜宵" in schedule_text:
            default_meal = "夜宵"
        rows: list[dict[str, Any]] = []
        rows.extend(self._extract_self_meal_events_from_text(detail.get("summary"), default_meal=default_meal, source="detail.summary"))
        for list_key in ("today_events", "state_variables"):
            raw_items = detail.get(list_key)
            if not isinstance(raw_items, list):
                continue
            for raw in raw_items[:12]:
                if isinstance(raw, dict):
                    text = raw.get("event") or raw.get("text") or raw.get("name") or raw.get("value") or raw.get("note")
                else:
                    text = raw
                rows.extend(self._extract_self_meal_events_from_text(text, default_meal=default_meal, source=f"detail.{list_key}"))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for meal_event in rows:
            key = f"{meal_event.get('meal')}:{meal_event.get('food')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meal_event)
        return deduped[:4]

    def _append_self_meal_log(
        self,
        meal_events: list[dict[str, Any]],
        *,
        segment: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not meal_events:
            return []
        now_ts = _now_ts()
        date_text = _single_line((plan or {}).get("date"), 16) or self._meal_log_date_key(now_ts)
        time_text = ""
        if isinstance(segment, dict):
            item = segment.get("item") if isinstance(segment.get("item"), dict) else {}
            time_text = _single_line(item.get("time") if isinstance(item, dict) else "", 20)
        log = self.data.setdefault("self_meal_log", [])
        if not isinstance(log, list):
            log = []
            self.data["self_meal_log"] = log
        existing_ids = {str(item.get("id") or "") for item in log if isinstance(item, dict)}
        added: list[dict[str, Any]] = []
        for meal_event in meal_events:
            meal = _single_line(meal_event.get("meal"), 20) or "加餐"
            food = _single_line(meal_event.get("food"), 60)
            if not food:
                continue
            base_id = hashlib.sha1(f"{date_text}|{time_text}|{meal}|{food}".encode("utf-8", errors="ignore")).hexdigest()[:16]
            meal_id = f"meal-{base_id}"
            if meal_id in existing_ids:
                continue
            entry = {
                "id": meal_id,
                "date": date_text,
                "time": time_text,
                "ts": now_ts,
                "occurred_at": self._meal_log_iso_time(now_ts),
                "meal": meal,
                "food": food,
                "source": _single_line(meal_event.get("source"), 40),
                "evidence": _single_line(meal_event.get("evidence"), 180),
                "memory_recorded": False,
                "memory_id": "",
            }
            log.append(entry)
            existing_ids.add(meal_id)
            added.append(entry)
        if len(log) > 160:
            del log[:-160]
        return added

    async def _memory_companion_record_self_meal(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict) or entry.get("memory_recorded"):
            return
        bridge = self._memory_companion_bridge()
        recorder = getattr(bridge, "record_persona_life", None) if bridge is not None else None
        if not callable(recorder):
            return
        date_text = _single_line(entry.get("date"), 16)
        time_text = _single_line(entry.get("time"), 20)
        meal = _single_line(entry.get("meal"), 20) or "加餐"
        food = _single_line(entry.get("food"), 60)
        if not food:
            return
        when = " ".join(part for part in (date_text, time_text) if part)
        content = f"Bot 在{when or date_text or '今天'}的{meal}吃了{food}。"
        try:
            memory_id = await recorder(
                content=content,
                scope="unknown",
                session_id="private_companion:self_meal",
                message_id=_single_line(entry.get("id"), 120),
                memory_id=f"private_companion_{_single_line(entry.get('id'), 80)}",
                metadata={
                    "date": date_text,
                    "time": time_text,
                    "event_type": "self_meal",
                    "action_label": "进食记录",
                    "meal": meal,
                    "food": food,
                    "evidence": _single_line(entry.get("evidence"), 180),
                    "source": _single_line(entry.get("source"), 40),
                    "query_anchors": ["self_meal", "吃了什么", "刚才吃了什么", "午餐", "晚餐", "早餐", "夜宵", food],
                },
                source_plugin="private_companion",
                confidence=0.78,
                importance=0.5,
                tags=["self_meal", "persona_life", "food", meal],
                occurred_at=_single_line(entry.get("occurred_at"), 80),
            )
        except Exception as exc:
            logger.debug("MemoryCompanion 进食记忆写入失败: %s", _single_line(exc, 120))
            return
        entry["memory_recorded"] = True
        entry["memory_id"] = _single_line(memory_id, 120)

    def _detect_care_feedback(self, text: str) -> dict[str, Any]:
        normalized = str(text or "").strip()
        if not normalized:
            return {"is_care": False, "tags": []}
        care_actions = r"吃药|喝药|去拿药|按时吃药|喝水|多喝热水|热水|温水|休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉|保暖|别着凉|穿厚|加衣服|盖好|难受|还好吗|没事吧|注意身体|照顾好自己|心疼"
        self_report = bool(
            re.search(
                rf"(?:我|俺|本人|这边|我们|咱们|咱).{{0,12}}(?:{care_actions})",
                normalized,
            )
        )
        if self_report:
            return {"is_care": False, "tags": []}
        tags: list[str] = []
        if re.search(r"吃药|喝药|去拿药|按时吃药", normalized):
            tags.append("medicine")
        if re.search(r"喝水|多喝热水|热水|温水", normalized):
            tags.append("water")
        if re.search(r"休息|早点睡|快睡|去睡|别熬夜|多睡会|睡一觉", normalized):
            tags.append("rest")
        if re.search(r"保暖|别着凉|穿厚|加衣服|盖好", normalized):
            tags.append("warm")
        if re.search(r"难受|还好吗|没事吧|注意身体|照顾好自己|心疼", normalized):
            tags.append("concern")
        tags = list(dict.fromkeys(tags))
        return {"is_care": bool(tags), "tags": tags}

    def _apply_care_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_care_feedback(text)
        if not feedback.get("is_care"):
            return False
        tags = feedback.get("tags", [])
        changed = False
        now = _now_ts()
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if str(cond.get("kind") or "") != "health":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0, _safe_float(cond.get("end_ts"), now) - now)
            shorten_ratio = 0.0
            if "medicine" in tags:
                shorten_ratio += 0.35
            if "rest" in tags:
                shorten_ratio += 0.2
            if "water" in tags:
                shorten_ratio += 0.12
            if "warm" in tags:
                shorten_ratio += 0.12
            if shorten_ratio > 0:
                cond["end_ts"] = now + remaining * max(0.35, 1 - min(shorten_ratio, 0.55))
                cond["duration_hours"] = max(
                    1,
                    int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600),
                )
                cond["energy_delta"] = min(-2, int(_safe_int(cond.get("energy_delta"), -8) * 0.75))
                cond["label"] = "收到照顾提醒后,不适强度略有下降"
                changed = True
            notes = cond.setdefault("care_notes", [])
            if isinstance(notes, list):
                care_note = "用户提供了关心反馈"
                if "medicine" in tags:
                    care_note = "用户提醒用药"
                elif "rest" in tags:
                    care_note = "用户提醒休息"
                elif "water" in tags:
                    care_note = "用户提醒补水"
                if care_note not in notes:
                    notes.append(care_note)
            cond["cause"] = _single_line(
                f"{_single_line(cond.get('cause'), 80)}；用户提供了照顾提醒".strip("；"),
                120,
            )
        if changed and random.random() < 0.72:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="被关心后的回暖",
                    label="收到用户关心后的轻度回暖",
                    mood="柔和",
                    energy_delta=6,
                    duration_hours=6,
                    intensity=72,
                    cause="用户提供了关心反馈",
                    phase="care_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=6,
                        cause="用户提供了关心反馈",
                        on_end_transition="",
                    ),
                )
            )
        return changed

    def _detect_food_feedback(self, text: str) -> dict[str, Any]:
        normalized = _single_line(text, 220)
        if not normalized:
            return {"is_food": False}
        food_markers = (
            "吃饭", "吃点", "吃些", "吃个", "吃什么", "吃啥", "晚饭", "晚餐", "午饭", "午餐",
            "早饭", "早餐", "夜宵", "外卖", "点餐", "做饭", "煮", "炒", "饭", "面", "粥",
            "汤", "菜", "肉", "蛋", "奶茶", "甜品", "水果", "火锅", "烧烤", "便当", "饺子",
            "馄饨", "米粉", "汉堡", "披萨", "三明治", "咖啡", "零食", "吃了", "吃过",
            "吃完", "吃饱", "饱了", "没吃", "还没吃", "饿", "嘴馋", "投喂", "喂你", "喂给你", "请你吃"
        )
        if not any(marker in normalized for marker in food_markers):
            return {"is_food": False}
        already_ate = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,10}(吃了|吃过|吃完|吃饱|饱了|喝了|喝过|喝完)", normalized)
            or re.search(r"^(吃了|吃过了|吃完了|吃饱了|饱了|喝完了)$", normalized)
        )
        food_nouns = r"(饭|菜|粥|汤|面|粉|饺子|馄饨|便当|外卖|夜宵|早餐|早饭|午餐|午饭|晚餐|晚饭|奶茶|咖啡|水果|零食|甜品|汉堡|披萨|三明治|火锅|烧烤|蛋|肉|吃的|喝的)"
        bot_subject = r"(你|bot|机器人|助手|ai|AI|宝宝|宝贝)"
        feeding = bool(
            re.search(r"(投喂|喂你|喂给你|给你投喂)", normalized, re.IGNORECASE)
            or re.search(fr"(给你|送你|递你|分你|留给你|请你|带你|陪你).{{0,12}}(吃|喝|点|买|做|煮|留|带|拿|叫|尝|来).{{0,12}}{food_nouns}?", normalized, re.IGNORECASE)
            or re.search(fr"(这个|这份|这杯|这碗|这口|这些).{{0,8}}(给你|分你|留给你).{{0,8}}(吃|喝|尝)?", normalized, re.IGNORECASE)
        )
        bot_food_question = bool(
            re.search(fr"{bot_subject}.{{0,10}}(想|要|打算|准备|喜欢|爱不爱|能不能|可以不可以)?.{{0,8}}(吃|喝|点).{{0,8}}(什么|啥|吗|嘛|么|哪[个家种些]?)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,8}}(饿了吗|饿不饿|吃饭了吗|吃了没|吃没吃|吃过了吗|想吃吗|要吃吗|喝吗)", normalized, re.IGNORECASE)
            or re.search(fr"{bot_subject}.{{0,10}}(要不要|想不想|吃不吃|喝不喝|点不点|饿不饿).{{0,10}}(吃|喝|点|饭|外卖|夜宵|奶茶|咖啡)?", normalized, re.IGNORECASE)
        )
        bot_directed = (not bot_food_question) and bool(
            re.search(fr"{bot_subject}.{{0,12}}(先|去|也|就|可以|要不|不如|还是|记得|别忘了|快|赶紧)?.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(推荐|建议).{{0,8}}{bot_subject}.{{0,12}}(吃|喝|点|煮|买|做|叫|尝)", normalized, re.IGNORECASE)
            or re.search(fr"(吃|喝|点|煮|买|做|叫|尝).{{0,10}}(给|给点|给买|给做).{{0,4}}{bot_subject}", normalized, re.IGNORECASE)
        )
        user_self_intent = bool(
            re.search(r"(我|俺|本人|这边|我们|咱们|咱).{0,14}(去|先|准备|要|想|打算|正在|刚|已经)?.{0,14}(吃|喝|点|买|做|煮|叫)", normalized)
            or re.search(r"(给我|帮我|我该|我要|我想|我能|我可以).{0,12}(吃|喝|点|买|做|煮|叫|推荐)", normalized)
        )
        user_menu_query = bool(
            re.search(r"(吃什么|吃啥|点什么|点啥|推荐).{0,10}(我|给我|一下)?", normalized)
            and re.search(r"(我|给我|帮我|吃什么|吃啥|点什么|点啥)", normalized)
        )
        implicit_bot_suggestion = bool(
            not already_ate
            and not bot_food_question
            and not user_self_intent
            and not user_menu_query
            and (
                re.search(r"(先|去|快|赶紧|记得|别忘了).{0,10}(吃|喝|点|买|做|煮|叫)", normalized)
                or re.search(r"(吃点|吃些|喝点|喝些).{0,8}(吧|呀|哦|噢)?$", normalized)
                or re.search(fr"(要不|不如|可以|试试).{{0,12}}(吃|喝|点|买|做|煮|叫).{{0,12}}{food_nouns}?", normalized)
            )
        )
        suggestion = bool(feeding or bot_directed or implicit_bot_suggestion)
        meal = ""
        for token, label in (("早餐", "早餐"), ("早饭", "早餐"), ("午餐", "午餐"), ("午饭", "午餐"), ("晚餐", "晚餐"), ("晚饭", "晚餐"), ("夜宵", "夜宵")):
            if token in normalized:
                meal = label
                break
        if not meal:
            hour = self._environment_now().hour
            if 10 <= hour < 15:
                meal = "午餐"
            elif 15 <= hour < 21:
                meal = "晚餐"
            elif hour >= 21 or hour < 3:
                meal = "夜宵"
            else:
                meal = "加餐"
        return {
            "is_food": True,
            "suggestion": suggestion,
            "actionable": suggestion,
            "already_ate": already_ate,
            "user_ate": already_ate,
            "feeding": feeding,
            "bot_directed": bot_directed,
            "bot_food_question": bot_food_question,
            "implicit_bot_suggestion": implicit_bot_suggestion,
            "meal": meal,
            "food_hint": _single_line(normalized, 80),
        }

    def _apply_food_feedback_to_state(self, text: str) -> bool:
        feedback = self._detect_food_feedback(text)
        if not feedback.get("is_food") or not feedback.get("actionable"):
            return False
        now = _now_ts()
        self.data["last_food_state_feedback_at"] = now
        self.data["last_food_state_feedback_text"] = _single_line(feedback.get("food_hint"), 120)
        changed = False
        conditions = self.data.setdefault("state_conditions", [])
        if not isinstance(conditions, list):
            self.data["state_conditions"] = []
            conditions = self.data["state_conditions"]
        for cond in conditions:
            if not isinstance(cond, dict) or str(cond.get("kind") or "") != "hunger":
                continue
            if _safe_float(cond.get("end_ts"), 0) <= now:
                continue
            remaining = max(0.0, _safe_float(cond.get("end_ts"), now) - now)
            if feedback.get("feeding"):
                target_remaining = max(5 * 60, min(remaining * 0.15, 12 * 60))
                label = "收到用户投喂后,饥饿感很快回落"
                cause = "用户投喂或分享吃的"
                energy_ratio = 0.25
            elif feedback.get("bot_directed"):
                target_remaining = max(8 * 60, min(remaining * 0.2, 20 * 60))
                label = "被提醒先吃点东西后,饥饿感开始回落"
                cause = "用户提醒去吃东西"
                energy_ratio = 0.35
            else:
                target_remaining = max(12 * 60, min(remaining * 0.3, 35 * 60))
                label = "有了吃什么的方向,饥饿感开始回落"
                cause = "用户给了饮食建议"
                energy_ratio = 0.45
            cond["end_ts"] = now + min(remaining, target_remaining)
            cond["duration_hours"] = max(1, int((cond["end_ts"] - _safe_float(cond.get("start_ts"), now)) / 3600))
            cond["mood"] = "回稳"
            cond["label"] = _single_line(label, 80)
            cond["cause"] = cause
            cond["phase"] = "food_feedback_resolving"
            current_delta = _safe_int(cond.get("energy_delta"), 0, -100, 100)
            if current_delta < 0:
                cond["energy_delta"] = min(0, int(current_delta * energy_ratio))
            changed = True
        if changed:
            conditions.append(
                self._make_condition(
                    kind="care_warmth",
                    title="饮食照顾回暖",
                    label="收到用户的投喂或吃饭提醒后,状态轻轻回稳",
                    mood="柔和",
                    energy_delta=4 if feedback.get("feeding") else 3,
                    duration_hours=2,
                    intensity=55,
                    cause=_single_line(feedback.get("food_hint"), 80),
                    phase="food_feedback",
                    transition_options=self._build_transition_options(
                        kind="care_warmth",
                        energy_delta=4 if feedback.get("feeding") else 3,
                        cause=_single_line(feedback.get("food_hint"), 80),
                        on_end_transition="",
                    ),
                )
            )
            self.data["daily_state"] = self._compose_state_from_conditions(self.data.get("daily_weather", {}))
        return changed

    def _meal_care_active_context(self, user: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        if not isinstance(user, dict):
            return {}
        context = user.get("meal_check_context")
        if not isinstance(context, dict) or not context.get("active"):
            return {}
        check_now = _now_ts() if now is None else now
        if str(context.get("date") or "") != _today_key() or (
            _safe_float(context.get("expires_at"), 0) > 0
            and check_now > _safe_float(context.get("expires_at"), 0)
        ):
            user["meal_check_context"] = {}
            return {}
        return context

    @staticmethod
    def _meal_reply_is_not_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        return bool(
            re.search(r"(?:还|一直|今天|刚刚|我)?没(?:有)?(?:吃|吃饭|吃上|来得及吃)|没呢|还没呢|没来得及|不准备吃|不想吃", compact)
        )

    @staticmethod
    def _meal_reply_is_non_food_consumption(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if not compact:
            return False
        prefix = r"(?:我|俺|咱|本人|今天|刚刚|刚才|已经|又|还|这次|这回)*"
        suffix = r"(?:了|啦|呢|呀|啊|哦|噢|哈)?"
        expression = (
            r"(?:吃(?:了|过|到)?(?:个|一(?:个|点|些))?"
            r"(?:亏|大亏|哑巴亏|苦头|闭门羹|官司|教训|排头|败仗|处分|罚单|巴掌|拳头|耳光|一惊|一吓|瘪|土|枪药))"
            r"|(?:(?:吃|服|喝)(?:了|过|完)?(?:点|些|一(?:片|粒|颗|包|支|瓶))?"
            r"(?:感冒药|退烧药|止痛药|消炎药|安眠药|胃药|中药|西药|处方药|降压药|抗生素|药片|药|胶囊|维生素|保健品|补剂))"
        )
        return bool(re.fullmatch(prefix + r"(?:才|就|可算)?" + expression + suffix, compact))

    @staticmethod
    def _meal_reply_confirms_eaten(text: str) -> bool:
        compact = re.sub(r"\s+", "", _single_line(text, 160))
        if (
            not compact
            or DailyStateMealPart01Mixin._meal_reply_is_not_eaten(compact)
            or DailyStateMealPart01Mixin._meal_reply_is_non_food_consumption(compact)
        ):
            return False
        return bool(
            re.search(r"(?:我|俺|咱|已经|刚刚|刚|早就|这边)?(?:吃了|吃过|吃完|吃饱|吃上了|用过餐|喝了|喝过)", compact)
            or re.search(r"(?:我|俺|咱)?(?:正在吃|在吃|开吃了)", compact)
            or compact in {"吃了", "吃过了", "吃完了", "吃饱了", "饱了", "刚吃", "刚吃完"}
        )
