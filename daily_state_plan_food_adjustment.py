# -*- coding: utf-8 -*-
"""DailyStatePlanFoodAdjustmentMixin。

由 tools/split_mixin_domain.py 从 daily_state_plan.py 机械抽取（8 个方法 + 0 个模块级名字 + 0 个类级赋值 / 499 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStatePlanMixin）。
"""
from __future__ import annotations

from .daily_state_plan_shared import _now_ts, _today_key, logger
from .daily_state_plan_shared import Any
from .daily_state_plan_shared import _safe_float
from .daily_state_plan_shared import _safe_int
from .daily_state_plan_shared import _single_line
from .daily_state_plan_shared import re



class DailyStatePlanFoodAdjustmentMixin:
    """DailyStatePlanFoodAdjustmentMixin（从 DailyStatePlanMixin 拆出）。"""


    @staticmethod
    def _schedule_food_reference_has_negative_preference(text: str) -> bool:
        if not text:
            return False
        negative_markers = ("不吃", "不喜欢", "不想吃", "不要", "别吃", "避开", "换别的", "代替", "别准备", "不要准备")
        food_markers = (
            "饭", "餐", "菜", "食物", "吃的", "早餐", "午饭", "晚饭", "夜宵", "零食",
            "排骨", "糖醋", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
        )
        return any(token in text for token in negative_markers) and any(token in text for token in food_markers)

    @staticmethod
    def _schedule_food_reference_is_concrete_motif(text: str) -> bool:
        if not text:
            return False
        food_markers = (
            "糖醋排骨", "排骨", "螺蛳粉", "锅包肉", "烤肠", "豆花", "冰粉", "甜口",
            "桂花", "奶茶", "豆浆", "夜宵", "饭团", "便当",
        )
        action_markers = ("准备", "带", "买", "做", "留", "夹", "点", "抢", "一起吃", "约饭", "饭")
        return any(food in text for food in food_markers) and any(action in text for action in action_markers)

    def _decay_schedule_food_reference_text(self, text: Any, *, field: str = "", dream: bool = False) -> str:
        source = _single_line(text, 260)
        if not source:
            return ""
        if dream:
            if self._schedule_food_reference_is_concrete_motif(source):
                return (
                    "梦境里可保留少量气味或颜色质感,但具体菜名属于近期高频意象,不要让它反推今天的餐食安排。"
                )
            return source
        clauses = [part.strip() for part in re.split(r"[；;。]+", source) if _single_line(part, 160)]
        if not clauses:
            clauses = [source]
        changed = False
        kept: list[str] = []
        added_guard = False
        for clause in clauses:
            cleaned = _single_line(clause, 180)
            if self._schedule_food_reference_has_negative_preference(cleaned):
                changed = True
                if not added_guard:
                    kept.append("若今天自然聊到餐食,只记得避开对方明确不吃的食物；不要为了这个避雷主动安排带饭、备餐或替代餐食剧情")
                    added_guard = True
                continue
            if self._schedule_food_reference_is_concrete_motif(cleaned):
                changed = True
                if not added_guard:
                    kept.append("具体食物只作近期聊过的软背景,不要连续复刻成今日午饭、带饭、留一份或邀约")
                    added_guard = True
                continue
            kept.append(cleaned)
        result = "；".join(part for part in kept if part).strip("；; ")
        if changed:
            logger.info(
                "已降级日程饮食参考: field=%s before=%s after=%s",
                field or "-",
                _single_line(source, 120),
                _single_line(result, 120),
            )
        return result or "只作轻微背景承接,不要强行改写今日主线。"

    @staticmethod
    def _normalize_schedule_adjustment_scope(scope: Any) -> str:
        text = _single_line(scope, 60)
        if any(token in text for token in ("主动策略", "主动消息", "主动频率")):
            return "proactive_only"
        if any(token in text for token in ("直到", "到家", "今晚到", "缓冲期")):
            return "until_condition"
        if any(token in text for token in ("今日后续", "今天剩余", "全天后续")):
            return "rest_of_day"
        if "下一段" in text:
            return "current_and_next"
        if any(token in text for token in ("当前段", "当前休息段")):
            return "current_only"
        return "current_and_next"

    def _format_schedule_adjustments_for_prompt(self, segment: dict[str, Any] | None = None) -> str:
        raw = self.data.get("schedule_adjustments", [])
        now = _now_ts()
        kept = []
        lines = []
        override_lines = []
        override = self._sleep_delay_override_state(now=now)
        if override:
            until_text = _single_line(override.get("until_text"), 24)
            override_lines.append(f"- 临时延后休息｜强｜今晚：到 {until_text} 前按用户临时陪聊约定处理,不要把当前睡眠段当成必须沉默。")
            override_lines.append("  承接要求：只影响今晚；到点后自然收声或回到休息,不要写成长期熬夜习惯。")
        outfit_override = self._current_dialogue_outfit_override(now=now)
        outfit_instruction = _single_line(outfit_override.get("instruction"), 180)
        if outfit_instruction:
            override_lines.append(
                f"- 用户换装｜强｜直到再次换装：用户已明确改变角色服装：“{outfit_instruction}”。"
            )
            override_lines.append(
                "  承接要求：把最新换装写入当前服装状态并延续到后续片段；日程或旧摘要里的默认穿搭只能补空白，不能把服装复原。"
            )
        if isinstance(raw, list) and raw:
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if _single_line(item.get("source_role"), 20) != "owner":
                    continue
                expires_at = _safe_float(item.get("expires_at"), 0)
                if expires_at > 0 and expires_at <= now:
                    continue
                date_text = _single_line(item.get("date"), 16)
                if date_text and date_text != _today_key():
                    continue
                kept.append(item)
                note = _single_line(item.get("note"), 120)
                source = _single_line(item.get("source"), 24)
                intensity = _single_line(item.get("intensity"), 16)
                scope = _single_line(item.get("scope"), 30)
                scope_key = _single_line(item.get("scope_key"), 24) or self._normalize_schedule_adjustment_scope(scope)
                if segment is None and scope_key == "proactive_only":
                    continue
                if isinstance(segment, dict):
                    target_index = _safe_int(segment.get("index"), -1, minimum=-1)
                    anchor_index = _safe_int(item.get("anchor_segment_index"), -1, minimum=-1)
                    if anchor_index < 0:
                        current_segment = self._current_detail_segment_for_update()
                        anchor_index = _safe_int((current_segment or {}).get("index"), target_index, minimum=-1)
                    if scope_key == "current_only" and target_index != anchor_index:
                        continue
                    if scope_key == "current_and_next" and target_index not in {anchor_index, anchor_index + 1}:
                        continue
                    if scope_key in {"rest_of_day", "until_condition", "proactive_only"} and target_index < anchor_index:
                        continue
                if note:
                    meta = "｜".join(part for part in (source or "互动", intensity, scope, f"作用域={scope_key}") if part)
                    lines.append(f"- {meta}：{note}")
                immediate = _single_line(item.get("immediate_reaction"), 120)
                if immediate:
                    lines.append(f"  即时反应：{immediate}")
                updates = item.get("state_updates")
                if isinstance(updates, list) and updates:
                    update_text = "；".join(
                        _single_line(update, 60)
                        for update in updates
                        if _single_line(update, 60)
                    )
                    if update_text:
                        lines.append(f"  状态变量更新：{update_text}")
                carry = _single_line(item.get("carry_rule"), 120)
                if carry:
                    lines.append(f"  承接要求：{carry}")
                if scope_key == "proactive_only":
                    lines.append("  作用限制：只允许影响 proactive_events，不得改写粗日程、summary、today_events、state_variables 或 presence_status。")
            if len(kept) != len(raw):
                self.data["schedule_adjustments"] = kept[-12:]
        if override_lines:
            lines.extend(override_lines)
        return "\n".join(lines[-12:]) if lines else "（暂无）"

    def _current_detail_state_variables(self) -> list[dict[str, str]]:
        segment = self._current_detail_segment_for_update()
        if not segment:
            return []
        enhanced = self.data.get("detail_enhanced_segments", {})
        if not isinstance(enhanced, dict):
            return []
        snapshot = enhanced.get(str(segment.get("key") or ""))
        if not isinstance(snapshot, dict):
            return []
        variables = snapshot.get("state_variables", [])
        if not isinstance(variables, list):
            return []
        return [item for item in variables if isinstance(item, dict)]

    def _detect_schedule_adjustment_from_interaction(self, text: str) -> dict[str, Any] | None:
        normalized = _single_line(text, 220)
        if not normalized:
            return None
        current_variables = self._current_detail_state_variables()
        variable_text = " ".join(
            f"{item.get('name', '')}:{item.get('value', '')} {item.get('note', '')}"
            for item in current_variables[:8]
        )
        def payload(
            *,
            source: str,
            note: str,
            immediate_reaction: str,
            state_updates: list[str],
            intensity: str = "中",
            scope: str = "当前段和下一段",
            carry_rule: str = "后续细化可根据这次用户介入留下合适的状态余味；若没有实际改变任务、作息、边界或共同场景，不必扩写成生活事件。",
            **extra: Any,
        ) -> dict[str, Any]:
            data = {
                "source": source,
                "note": note,
                "immediate_reaction": immediate_reaction,
                "state_updates": state_updates,
                "intensity": intensity,
                "scope": scope,
                "carry_rule": carry_rule,
                "user_text": normalized,
            }
            data.update(extra)
            return data

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        sleep_delay = self._detect_sleep_delay_request(normalized)
        if sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24)
            return payload(
                source="临时延后休息",
                note=f"用户今晚希望晚点休息或陪聊；到 {until_text} 前暂时不要把睡眠段当成必须沉默,但这只是今晚的临时约定。",
                immediate_reaction="Bot 会把今晚的节奏稍微放慢并留出陪聊余地,但不会把这当成长期作息改变。",
                state_updates=[f"休息安排：今晚临时延后到 {until_text}", "清醒程度：陪聊但低负担", "后续安排：到点后自然收声或睡回去"],
                intensity="强",
                scope=f"今晚到 {until_text}",
                carry_rule="只影响今晚和当前休息段；后续细化可以保留轻微陪聊/等待感,但不得把它写成长期熬夜习惯,到点后应自然收声或回到休息。",
                sleep_delay_until_ts=sleep_delay.get("until_ts"),
                sleep_delay_until_text=until_text,
                sleep_delay_explicit_time=bool(sleep_delay.get("explicit_time")),
            )
        is_actual_rest_segment = (
            self._sleep_rest_window_active()
            and not self._sleep_delay_override_state(clear_expired=True)
            and self._is_sleepy_plan_item(current_item)
        )
        if is_actual_rest_segment and not re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            runtime_before = self._sleep_runtime_state()
            last_woken = _safe_float(runtime_before.get("last_woken_at"), _safe_float(runtime_before.get("updated_at"), 0))
            last_user_text = _single_line(runtime_before.get("last_user_text"), 80)
            current_user_text = _single_line(normalized, 80)
            consumed_at = _safe_float(runtime_before.get("last_wakeup_context_consumed_at"), 0)
            same_wakeup_message = bool(
                runtime_before.get("phase") == "woken"
                and last_user_text
                and last_user_text == current_user_text
                and consumed_at < last_woken
                and _now_ts() - last_woken < 120
            )
            if same_wakeup_message:
                runtime_before["last_wakeup_context_consumed_at"] = _now_ts()
                return payload(
                    source="睡眠中被用户唤醒",
                    note="当前日程处于休息/睡眠段,这条消息已经在休息闸门放行时登记为唤醒；不要重复计数,语气只保留刚醒的慢一点和轻一点。",
                    immediate_reaction="Bot 刚被这条消息轻轻叫醒,会慢一点看清内容再回应。",
                    state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                    intensity="强",
                    scope="当前休息段和后续短时间",
                    carry_rule="回复可以带一点刚醒的气息,但不得降低理解、事实和回答质量；不要再表现成又被叫醒一次。",
                )
            within_awake_grace = (
                runtime_before.get("phase") == "woken"
                and self._sleep_awake_grace_seconds() > 0
                and _now_ts() - last_woken < self._sleep_awake_grace_seconds()
            )
            if within_awake_grace:
                runtime_before["last_user_text"] = _single_line(normalized, 80)
                return payload(
                    source="睡眠中醒后续聊",
                    note="当前日程仍是休息/睡眠段,但 Bot 已在醒后缓冲期内；这是被叫醒后的连续对话,不再按再次唤醒处理。",
                    immediate_reaction="Bot 还没完全精神起来,但已经在接着聊天,不会每句话都像重新被吵醒。",
                    state_updates=["清醒程度：醒后续聊/慢慢清醒", "语气：仍轻一点,但不重复表演被叫醒", "后续安排：停聊后再自然睡回去"],
                    intensity="中",
                    scope="醒后缓冲期",
                    carry_rule="后续回复保持连续聊天感,不要写成每条消息都重新惊醒；用户继续聊时可以逐渐清醒一点。",
                )
            sleep_runtime = self._mark_sleep_woken_by_user(normalized)
            prior_wakes = 0
            segment = self._current_detail_segment_for_update()
            enhanced = self.data.get("detail_enhanced_segments", {})
            if isinstance(segment, dict) and isinstance(enhanced, dict):
                snapshot = enhanced.get(str(segment.get("key") or ""))
                updates = snapshot.get("interaction_updates", []) if isinstance(snapshot, dict) else []
                if isinstance(updates, list):
                    prior_wakes = sum(
                        1 for update in updates
                        if isinstance(update, dict) and "唤醒" in str(update.get("source") or "")
                    )
            prior_wakes = max(prior_wakes, _safe_int(sleep_runtime.get("woken_count"), 1, 1) - 1)
            if prior_wakes > 0:
                return payload(
                    source="睡眠中再次被唤醒",
                    note="当前日程处于休息/睡眠段,用户又发来消息；回复语气应带一点被重新叫醒的迟钝感,但必须清楚理解用户的话,不要埋怨用户。若用户继续聊,可以慢慢醒一点；若用户停下,Bot 会很快继续睡回去。",
                    immediate_reaction="Bot 又被消息轻轻拽醒一下,语气会慢半拍,但会看清用户说了什么再回应。",
                    state_updates=["清醒程度：再次被唤起/半梦半醒", "语气：慢半拍、短一点", "后续安排：用户不继续打扰就继续睡"],
                    intensity="中",
                    scope="当前休息段",
                    carry_rule="当前段回复必须有刚被重新唤起的语气感觉,但不得降低理解和回答质量；如果后续没有用户消息,下一段细化应让 Bot 继续休息或睡回去。",
                )
            return payload(
                source="睡眠中被用户唤醒",
                note="当前日程处于休息/睡眠段,用户发来消息把 Bot 轻轻叫醒；回复语气应像刚醒或半梦半醒,不要立刻精神饱满,但必须看懂并正面回应用户。若用户没有继续打扰,后续应自然睡回去或继续休息。",
                immediate_reaction="Bot 会先带着睡意看一眼消息,语气慢一点,但不会乱接或漏掉用户真正的问题。",
                state_updates=["清醒程度：刚被唤醒/迷糊", "语气：轻、短、带睡意", "后续安排：用户不继续打扰就继续睡"],
                intensity="强",
                scope="当前休息段和后续短时间",
                carry_rule="回复与后续细化必须承接“刚被用户唤醒”的语气状态,但不得牺牲理解、事实和回答质量；如果没有连续聊天,不要强行清醒活动,要睡回去或继续休息。",
            )

        if re.search(r"换元|代入|公式|思路|解法|答案|步骤|这题|函数题|数学题|阅读题|作文|作业|代码|报错|bug|怎么写|这样做|你试试|应该是", normalized):
            updates = ["学习/作业进度：因用户提供思路而推进", "情绪：卡住->松一口气"]
            if re.search(r"卡|函数|数学|作业", variable_text):
                updates.insert(0, "卡住点：用户提示后得到缓解")
            return payload(
                source="用户帮助",
                note="用户刚刚提供了和当前任务相关的帮助；当前任务不再按原本卡住状态推进,后续时间段要继承“被帮助后松动/推进”的结果。",
                immediate_reaction="看到用户给的思路后,Bot 先照着试了一下；如果确实对上当前任务,心里会明显松一口气。",
                state_updates=updates,
                intensity="强",
                scope="当前段、下一段、今日后续",
                carry_rule="如果后续仍写学习/作业/任务,必须体现已经被用户提示影响过：进度推进、情绪松动、或换了做法。",
            )
        food_feedback = self._detect_food_feedback(normalized)
        if food_feedback.get("is_food") and (food_feedback.get("suggestion") or food_feedback.get("bot_directed")):
            meal = _single_line(food_feedback.get("meal"), 20) or "饭点"
            food_hint = _single_line(food_feedback.get("food_hint"), 80)
            return payload(
                source="饮食反馈",
                note=f"用户刚刚给了关于{meal}或吃什么的反馈；后续日程要把这当成一个已经影响生活安排的小事实,可以调整饭点、胃口、准备食物或晚餐选择。",
                immediate_reaction="Bot 会把这句当成一个可执行的小主意,刚才悬着的“吃什么”落下来一点。",
                state_updates=[f"{meal}选择：受用户反馈影响", "饥饿感：开始回落", "后续安排：可能按反馈调整吃饭/点餐/做饭"],
                intensity="中",
                scope="当前段和今日后续饭点",
                carry_rule=f"后续细化如果涉及吃饭、晚餐、休息或外出,要自然承接用户这句饮食反馈：{food_hint}。不要生硬复述,也不要像没有问过一样重置。",
            )
        self_reported_care_action = bool(
            re.search(
                r"(?:我|俺|本人|这边|我们|咱们|咱).{0,12}(?:去睡|早点睡|睡觉|休息|先洗澡|先吃饭|吃点|喝水|躺会|停一下|歇会)",
                normalized,
            )
        )
        if not self_reported_care_action and re.search(r"去睡|早点睡|睡觉|休息|别写了|别弄了|先洗澡|先吃饭|吃点|喝水|别熬|躺会|停一下|歇会", normalized):
            return payload(
                source="用户照顾",
                note="用户刚刚给了休息或照顾指令；后续节奏应明显调慢,更可能提前收尾、补充休息、喝水吃饭或把任务延后。",
                immediate_reaction="Bot 看到这句会停一下手里的事,嘴上可能不立刻答应,但动作会慢下来一点。",
                state_updates=["体力：消耗放缓/略微回稳", "情绪：被照顾后的柔和", "后续安排：更倾向提前收尾或补充休息"],
                intensity="强",
                scope="当前段和今日后续",
                carry_rule="下一段不能完全无视这句照顾提醒；至少要在节奏、体力或收尾方式上留下影响。",
            )
        shared_location_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|走吧|出发吧", normalized))
        outward_action_signal = bool(re.search(r"出发|出门|出去|去吃|去逛|去买|去玩|上车|下车|走了|走起|换鞋|拿钥匙|等车|打车|坐车|地铁|公交|到了|排队|找位子|点单|点餐|下单", normalized))
        if shared_location_signal and outward_action_signal:
            self._apply_dialogue_location_override("外面")
            return payload(
                source="用户带出/同行",
                note="用户刚刚带角色出门或一起外出；当前位置应从家里切换到外面,后续细化要承接外出场景,不要把角色写回家里。",
                immediate_reaction="Bot 会赶紧收拾一下东西,跟着用户往外走,可能边走边看手机或整理衣服。",
                state_updates=["位置：家里->外面", "活动：跟随用户外出", "情绪：略兴奋或期待"],
                intensity="强",
                scope="当前段和今日后续直到回家线索出现",
                carry_rule="后续细化和状态注入必须把角色位置保持在'外面',直到用户明确说回家、到家或日程自然过渡到居家时段；不要把角色写回沙发、卧室或家里。",
            )
        shared_return_signal = bool(re.search(r"一起|我们|咱们|咱俩|带你|带我|陪你|陪我|跟你|跟我|回家吧|送你回", normalized))
        return_home_signal = bool(re.search(r"回来了|到家了|回家了|进家门|开门|进门|回到.*家|到家|安全到家", normalized))
        if shared_return_signal and return_home_signal:
            self._apply_dialogue_location_override("家里")
            return payload(
                source="用户带回/回家",
                note="用户和角色刚刚回到家；当前位置应从外面切换回家里,后续细化要承接回家后场景。",
                immediate_reaction="Bot 会松一口气,可能踢掉鞋子或把东西放下,瘫到沙发上。",
                state_updates=["位置：外面->家里", "活动：回到居家", "情绪：放松"],
                intensity="中",
                scope="当前段和下一段",
                carry_rule="后续细化可以把角色写回家里场景,但不要立刻恢复出门前的精确活动,要体现外出后的余味。",
            )
        explicit_appointment_signal = bool(re.search(
            r"(?:约好|说好|定了|晚点(?:一起|聊|打电话|语音|开黑|看)|(?:一起|我们|咱们|咱俩|陪你|陪我|等你|等我|跟你|跟我).{0,18}(?:待会|一会|晚上|明天|等下|见面|打电话|语音|开黑|看))",
            normalized,
        ))
        if explicit_appointment_signal:
            return payload(
                source="用户约定",
                note="用户刚刚给出陪伴、等待、稍后一起做事或约定线索；后续日程应保留期待/等待/预留空档,不要像没有约定一样推进。",
                immediate_reaction="Bot 会把这句话在心里放一下,后续做事时更容易分神看时间或看消息。",
                state_updates=["等待/期待：上升", "后续安排：可能预留空档", "主动话题：围绕约定轻轻回勾"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续主动契机和细化事件要把这个约定当作开放话头,可以等待、预留时间或轻轻提醒。",
            )
        care = self._detect_care_feedback(normalized)
        if care.get("is_care"):
            return payload(
                source="用户关心",
                note="用户刚刚给了关心或照顾提醒；后续日程需要从低落/烦躁向被安抚后的柔和、慢慢回稳偏移,不要升级破坏性冲动。",
                immediate_reaction="Bot 看到关心会先顿一下,语气和动作都比刚才软一点。",
                state_updates=["情绪：低落/烦躁->被接住一点", "体力：主观疲惫感略降"],
                intensity="中",
                scope="当前段、下一段、情绪底色",
            )
        if re.search(r"摸摸|贴贴|抱抱|亲亲|揉揉|蹭蹭|摸头|陪你|哄你|乖|不难过|别难过|没关系|辛苦了|抱一下", normalized):
            return payload(
                source="安慰互动",
                note="用户刚刚在安慰或亲近；后续日程应保留一点被接住的余温,表达更软一些,不要继续单向累积负面情绪。",
                immediate_reaction="Bot 会把刚才绷着的劲松下来一点,可能短暂地想贴近用户。",
                state_updates=["情绪：紧绷->柔和", "亲近感：上升"],
                intensity="中",
                scope="当前段和情绪底色",
            )
        if re.search(r"别生气|不要烦|冷静|别急|别砸|别摔|别骂|别打", normalized):
            return payload(
                source="边界提醒",
                note="用户提醒降低攻击性或破坏性表达；后续日程必须把烦躁写成收着的动作,避免砸、摔、扔、打这类冲动。",
                immediate_reaction="Bot 会把那点冲劲压回去,改成少说两句或把东西放远一点。",
                state_updates=["情绪：冲动->收住", "行为边界：避免破坏性动作"],
                intensity="强",
                scope="今日后续",
                carry_rule="后续所有细化都必须遵守这个边界,不能再输出破坏性动作或攻击性冲动。",
            )
        if re.search(r"别吵|别发|别找|安静|闭嘴|先别|不要来|忙|我有事|没空", normalized):
            return payload(
                source="用户边界",
                note="用户刚刚表达边界或忙碌；后续主动应明显收敛,减少追问和贴近,让日程转向自我消化。",
                immediate_reaction="Bot 会把原本想继续靠近的动作收住,把消息窗口放到一边。",
                state_updates=["主动欲：下降", "关系状态：后退一点", "后续安排：转向自我消化"],
                intensity="强",
                scope="今日后续主动策略",
                carry_rule="后续主动消息必须降低频率和压迫感,不要把边界当作可撒娇突破的对象。",
            )
        return None

    def _record_schedule_adjustment_from_interaction(self, text: str, user: dict[str, Any] | None = None) -> bool:
        if self._private_user_role(user) != "owner":
            return False
        outfit_updated = self._record_dialogue_outfit_override_from_interaction(text, user)
        adjustment = self._detect_schedule_adjustment_from_interaction(text)
        if not adjustment:
            return outfit_updated
        raw = self.data.setdefault("schedule_adjustments", [])
        if not isinstance(raw, list):
            raw = []
            self.data["schedule_adjustments"] = raw
        note = _single_line(adjustment.get("note"), 140)
        if not note:
            return False
        now = _now_ts()
        intensity = _single_line(adjustment.get("intensity"), 16) or "中"
        ttl_hours = 18 if intensity == "强" else 10 if intensity == "中" else 6
        current_segment = self._current_detail_segment_for_update()
        anchor_index = _safe_int((current_segment or {}).get("index"), -1, minimum=-1)
        scope_key = self._normalize_schedule_adjustment_scope(adjustment.get("scope"))
        source = _single_line(adjustment.get("source"), 24)
        if source == "用户带回/回家":
            raw[:] = [
                old
                for old in raw
                if not (
                    isinstance(old, dict)
                    and (
                        _single_line(old.get("condition_key"), 32) == "return_home"
                        or (
                            _single_line(old.get("scope_key"), 24) == "until_condition"
                            and "回家" in _single_line(old.get("scope"), 60)
                        )
                    )
                )
            ]
        item = {
            "date": _today_key(),
            "source": source,
            "note": note,
            "immediate_reaction": _single_line(adjustment.get("immediate_reaction"), 140),
            "state_updates": adjustment.get("state_updates", []),
            "user_text": _single_line(adjustment.get("user_text"), 120),
            "intensity": intensity,
            "scope": _single_line(adjustment.get("scope"), 40),
            "scope_key": scope_key,
            "carry_rule": _single_line(adjustment.get("carry_rule"), 160),
            "source_role": "owner",
            "source_user_id": _single_line((user or {}).get("user_id"), 80),
            "created_at": now,
            "expires_at": now + ttl_hours * 3600,
        }
        if anchor_index >= 0:
            item["anchor_segment_index"] = anchor_index
            item["anchor_segment_key"] = _single_line((current_segment or {}).get("key"), 120)
        if scope_key == "until_condition" and (source == "用户带出/同行" or "回家" in item["scope"]):
            item["condition_key"] = "return_home"
        sleep_delay_until = _safe_float(adjustment.get("sleep_delay_until_ts"), 0)
        if sleep_delay_until > now:
            item["sleep_delay_until_ts"] = sleep_delay_until
            item["sleep_delay_until_text"] = _single_line(adjustment.get("sleep_delay_until_text"), 24)
            item["sleep_delay_explicit_time"] = bool(adjustment.get("sleep_delay_explicit_time"))
            self._apply_sleep_delay_override(
                {
                    "until_ts": sleep_delay_until,
                    "until_text": item["sleep_delay_until_text"],
                    "explicit_time": item["sleep_delay_explicit_time"],
                    "user_text": item["user_text"],
                },
                text=item["user_text"],
            )
        self._record_detail_interaction_update(item)
        plan = self.data.get("daily_plan", {})
        current_item = self._get_current_plan_item(plan) if isinstance(plan, dict) else None
        if isinstance(current_item, dict):
            current_item["lifecycle_status"] = "changed"
            current_item["changed_at"] = self._environment_now().strftime("%H:%M")
            current_item["change_reason"] = _single_line(adjustment.get("source") or note, 80)
        if raw and isinstance(raw[-1], dict) and raw[-1].get("note") == note:
            raw[-1].update(item)
        else:
            raw.append(item)
            del raw[:-12]
        self._invalidate_detail_after_interaction(now=now)
        return True
