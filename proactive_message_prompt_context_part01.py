# -*- coding: utf-8 -*-
"""ProactiveMessagePromptContextPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_prompt_context.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 488 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessagePromptContextMixin）。
"""
from __future__ import annotations
from .proactive_message_prompt_context_shared import Any
from .proactive_message_prompt_context_shared import PromptRenderMode
from .proactive_message_prompt_context_shared import PromptSection
from .proactive_message_prompt_context_shared import _safe_int
from .proactive_message_prompt_context_shared import _single_line
from .proactive_message_prompt_context_shared import datetime
from .proactive_message_prompt_context_shared import prompt_section
from .proactive_message_prompt_context_shared import re
from .proactive_message_prompt_context_shared import render_prompt_sections
from .proactive_message_prompt_context_shared import runtime_persona_setting



class ProactiveMessagePromptContextPart01Mixin:
    """ProactiveMessagePromptContextPart01Mixin（从 ProactiveMessagePromptContextMixin 拆出）。"""


    def _format_state_for_framework_prompt(self, state: dict[str, Any], *, reason: str, action: str) -> str:
        if not isinstance(state, dict):
            return "只作为语气底色：整体平稳,不要在正文里汇报状态。"
        parts: list[str] = []
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 20)
        weather = _single_line(state.get("weather"), 40)
        conditions = state.get("conditions")
        meaningful_conditions: list[str] = []
        if isinstance(conditions, list):
            for cond in conditions:
                if not isinstance(cond, dict):
                    continue
                if not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("kind"), 16)
                text = _single_line(cond.get("text"), 28)
                if label and text:
                    meaningful_conditions.append(f"{label}/{text}")
        if meaningful_conditions:
            parts.append(
                "语气里带一点"
                + "、".join(meaningful_conditions[:2])
                + "的影响,但不要主动解释这些状态。"
            )
        elif energy <= 42:
            parts.append("语气短一点、慢一点；不要直接说状态标签、数值或内部原因。")
        elif energy >= 85:
            parts.append("语气可以轻快一点,但不要直接说自己精神很好。")
        elif mood and mood not in {"平稳", "中性"}:
            parts.append(f"语气底色偏{mood},让它自然露出来,不要直接汇报情绪。")
        if "photo_text" in action or reason in {"activity_share", "diary_share", "evening_greeting", "morning_greeting"}:
            if weather and weather not in {"暂无天气信息"}:
                parts.append(
                    f"天气只作为内部的光线/画面感参考：{weather}。"
                    "不要在正文或语音里提天气、气温、下雨或天色，也不要追问对方那边的天气；"
                    "真正的环境突变和官方预警会由独立主动原因提供明确事实。"
                )
        parts.append(
            "状态只影响语气、用词、句子长短、是否开口和话题选择；不要为了表现状态而写动作小剧场。"
        )
        parts.append(
            "如果一句话已经问候、关心或递出了具体片段,可以直接停住；不用为了显得日常,在后半句补“我刚才在发呆/躺着/盯天花板”这类状态汇报。"
        )
        parts.append(
            "即使状态是困倦、迷糊、半梦半醒或低能量,也只能让语气更轻更慢；不能降低理解质量、事实判断或正常承接能力。"
        )
        parts.append(
            "不要直接宣告“我累了/我吓到了/我在写作业”,也不要用“茶差点打翻/笔帽掉了/喝水呛到”这类动作表演状态。确实要表达时只用最短口语,如“困了”“别说了”。"
        )
        return "；".join(parts) if parts else "只作为语气底色：整体平稳,不要在正文里汇报状态。"

    def _proactive_expression_shape_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
    ) -> str:
        """让当前能量影响表达形状，而不是只影响是否发送。"""
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy"), 70, 0, 100) if isinstance(state, dict) else 70
        lines = [
            "主动消息通常比正式回复更碎、更口语；大多数控制在 20 个汉字左右，能停在半句就不要补成完整段落。",
            "除非语义确实需要，不要每句都用完整句号收尾；不要为了显得自然而堆叠解释、背景和客套。",
        ]
        if energy <= 42:
            lines.append("当前能量偏低：优先一条短句或一个轻问候，少用感叹号，最多一到两段，不要连续铺陈。")
        elif energy >= 85:
            lines.append("当前能量偏高：可以用两三个很短的口语句连着说，允许轻微兴奋感，但不要写成长段独白。")
        else:
            lines.append("当前能量平稳：优先一到两句短消息，保留一点停顿和留白，避免客服式完整陈述。")
        if reason in {"check_in", "quiet_care", "state_share"} and "photo" not in action:
            lines.append("如果本轮只是想起对方或顺手分享一点感受，低信息量的短句也可以成立；不要为了增加信息而硬塞新事实。")
        busy_context_getter = getattr(self, "_busy_proactive_voice_context", None)
        try:
            busy_context = busy_context_getter() if callable(busy_context_getter) else {}
        except Exception:
            busy_context = {}
        if isinstance(busy_context, dict) and busy_context.get("busy"):
            lines.append(
                "当前日程处于忙碌片段：如果这轮只是轻量问候、感受或一句顺手分享，"
                "可以把语音当作更省手的表达；语音脚本保持一两句、口语化，不要朗读长说明。"
            )
            lines.append(
                "忙碌只提供表达倾向，不是强制动作；涉及命令、链接、重要事实、配置或需要留档的信息，继续用文字。"
            )
        if action == "message" and self._proactive_reaction_expression_enabled(action):
            lines.append(
                "如果正文只是‘收到/好的/笑死/辛苦了’这类语义明确的短回应，"
                "可以考虑在正文之后追加一个匹配情绪的语义表情标签；正文较长、信息重要或语气不确定时不要追加。"
            )
        return prompt_section(
            key="proactive.expression_shape",
            title="主动消息的表达形状",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _proactive_expression_shape_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
    ) -> str:
        return render_prompt_sections(
            [self._proactive_expression_shape_prompt_section(user, reason=reason, action=action)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_plan_item_for_framework_prompt(self, item: dict[str, Any] | None) -> str:
        if not isinstance(item, dict):
            return ""
        activity = _single_line(item.get("activity"), 60)
        mood = _single_line(item.get("mood"), 12)
        time_text = _single_line(item.get("time"), 12)
        if activity:
            activity = re.sub(r"[,、]?\s*想起了[^,。]+", "", activity).strip(",。 ")
            activity = re.sub(r"[,、]?\s*突然想到[^,。]+", "", activity).strip(",。 ")
        parts = []
        if time_text:
            parts.append(time_text)
        if activity:
            parts.append(activity)
        if mood and mood not in {"平稳", "中性"}:
            parts.append(f"情绪偏{mood}")
        return "｜".join(parts)

    def _nearby_plan_items(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        plan = plan if isinstance(plan, dict) else self.data.get("daily_plan", {})
        if not isinstance(plan, dict) or not self._is_plan_date_active(plan.get("date")):
            return {}
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            return {}
        now_minutes = self._effective_plan_now_minutes(str(plan.get("date") or ""))
        if now_minutes is None:
            return {}
        # The raw daily plan is an edit input.  Build a purpose-specific view
        # before exposing any text to proactive generation so past/current
        # facts require evidence and future scene prose is reduced to a small
        # labelled summary.
        current_ids: set[str] = set()
        history_ids: set[str] = set()
        proactive_entries: dict[str, dict[str, Any]] = {}
        disclosure_available = callable(getattr(self, "_agenda_disclosure_view", None))
        disclosure_keys: dict[tuple[str, str], str] = {}
        disclosure = getattr(self, "_agenda_disclosure_view", None)
        if callable(disclosure):
            try:
                for purpose, bucket in (("current_fact", current_ids), ("history_fact", history_ids)):
                    view = disclosure(purpose, max_entries=64)
                    values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                    for value in values if isinstance(values, list) else []:
                        if isinstance(value, dict):
                            key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                            if key:
                                bucket.add(key)
                            pair = (
                                _single_line(value.get("time"), 12),
                                _single_line(value.get("title") or value.get("activity"), 120),
                            )
                            if pair[0] and pair[1] and key:
                                disclosure_keys[pair] = key
                view = disclosure("proactive", max_entries=64)
                values = view.get("entries", []) if isinstance(view, dict) else getattr(view, "entries", [])
                for value in values if isinstance(values, list) else []:
                    if isinstance(value, dict):
                        key = str(value.get("plan_id") or value.get("entry_id") or "").strip()
                        if key:
                            proactive_entries[key] = value
                        pair = (
                            _single_line(value.get("time"), 12),
                            _single_line(value.get("title") or value.get("activity"), 120),
                        )
                        if pair[0] and pair[1] and key:
                            disclosure_keys[pair] = key
            except Exception:
                current_ids.clear()
                history_ids.clear()
                proactive_entries = {}
                disclosure_keys = {}
                # The policy is a disclosure firewall.  If it is present but
                # unavailable, fail closed instead of falling back to raw
                # daily-plan prose in a proactive prompt.
                disclosure_available = True
        parsed: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            minute = self._parse_hhmm_to_minutes(item.get("time"))
            if minute is None:
                continue
            item_key = str(item.get("plan_id") or "").strip()
            if not item_key and disclosure_available:
                pair = (
                    _single_line(item.get("time"), 12),
                    _single_line(item.get("activity") or item.get("title"), 120),
                )
                item_key = disclosure_keys.get(pair, "")
            if disclosure_available and minute <= now_minutes and item_key not in current_ids and item_key not in history_ids:
                # A clock window alone is not a current/history fact.  This
                # also prevents an unexecuted past plan from being presented
                # as the "previous" item in proactive prompts.
                continue
            if disclosure_available and minute > now_minutes:
                public = proactive_entries.get(item_key)
                if not public:
                    # The proactive view applies its horizon and commitment
                    # gates.  Do not fall back to raw future scene prose.
                    continue
                safe = dict(item)
                safe["activity"] = _single_line(public.get("title"), 100) or "临近时段可能有安排"
                safe["message_seed"] = ""
                safe["scene"] = ""
                safe["candidates"] = []
                item = safe
            parsed.append((minute, item))
        if not parsed:
            return {}
        parsed.sort(key=lambda pair: pair[0])
        previous: tuple[int, dict[str, Any]] | None = None
        upcoming: tuple[int, dict[str, Any]] | None = None
        for minute, item in parsed:
            if minute <= now_minutes:
                previous = (minute, item)
                continue
            upcoming = (minute, item)
            break
        return {
            "now_minutes": now_minutes,
            "previous": previous[1] if previous else None,
            "previous_age": now_minutes - previous[0] if previous else None,
            "upcoming": upcoming[1] if upcoming else None,
            "upcoming_in": upcoming[0] - now_minutes if upcoming else None,
        }

    def _format_schedule_context_for_prompt(self, plan: dict[str, Any] | None = None) -> str:
        nearby = self._nearby_plan_items(plan)
        if not nearby:
            return ""
        previous = nearby.get("previous")
        upcoming = nearby.get("upcoming")
        previous_age = nearby.get("previous_age")
        upcoming_in = nearby.get("upcoming_in")
        lines: list[str] = []
        if isinstance(upcoming, dict) and isinstance(upcoming_in, int) and 0 <= upcoming_in <= 45:
            lines.append(
                "即将进入："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 90:
                prev_mood = _single_line(previous.get("mood"), 24)
                prev_time = _single_line(previous.get("time"), 12)
                lines.append(
                    f"上一段只作余味：{prev_time}"
                    + (f"｜情绪：{prev_mood}" if prev_mood else "")
                    + "。不要把上一段当成正在发生。"
                )
        elif isinstance(previous, dict) and isinstance(previous_age, int) and previous_age <= 75:
            lines.append(
                "当前/最近："
                + self._format_plan_item_for_prompt(previous)
                + f"（约 {previous_age} 分钟前开始）"
            )
            if isinstance(upcoming, dict) and isinstance(upcoming_in, int):
                lines.append(
                    "下一段参考："
                    + self._format_plan_item_for_prompt(upcoming)
                    + f"（约 {upcoming_in} 分钟后）"
                )
        elif isinstance(upcoming, dict) and isinstance(upcoming_in, int):
            lines.append(
                "附近更应参考下一段："
                + self._format_plan_item_for_prompt(upcoming)
                + f"（约 {upcoming_in} 分钟后）"
            )
            if isinstance(previous, dict):
                lines.append("上一段已经过去较久,只保留很淡的情绪余味,不要复述场景。")
        elif isinstance(previous, dict):
            lines.append(
                "最近一段："
                + self._format_plan_item_for_prompt(previous)
                + "。如果离当前时间较久,只当作余味。"
            )
        return "\n".join(line for line in lines if line)

    def _sanitize_schedule_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        cleaned = self._sanitize_owner_environment_context_for_private_user(cleaned, user)
        sensitive_names = [
            _single_line(item, 24)
            for item in (
                runtime_persona_setting(self, "default_nickname", ""),
                *(getattr(self, "target_user_ids", []) or []),
            )
            if _single_line(item, 24)
        ]
        for name in sensitive_names:
            cleaned = cleaned.replace(name, "某个熟人")
        cleaned = re.sub(r"看见[^，。,；;。！？]{1,24}坐在[^，。,；;。！？]{0,24}", "看见有人在忙", cleaned)
        cleaned = re.sub(r"(?:放在|放到|搁在|塞到)[^，。,；;。！？]{0,12}(?:桌边|桌上|手边|旁边)", "放到一边", cleaned)
        cleaned = re.sub(r"给你[^，。,；;。！？]{0,24}", "给熟人留了一点小东西", cleaned)
        cleaned = re.sub(r"你(?:的|那边|桌边|桌上|手边)", "对方那边", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _sanitize_owner_environment_context_for_private_user(self, text: str, user: dict[str, Any] | None = None) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        if self._private_user_role(user) != "friend":
            return cleaned
        weather_tokens = (
            "天气", "气温", "温度", "湿度", "降雨", "下雨", "阵雨", "小雨", "中雨", "大雨",
            "暴雨", "雷雨", "雷暴", "晴", "多云", "阴天", "风速", "风力", "空气质量", "OpenWeather",
        )
        location_tokens = (
            "当前位置", "当前地点", "所在地", "所在城市", "住处", "住址", "地址", "城市", "小区",
            "街道", "门牌", "宿舍", "校区", "位置：", "地点：", "外面在",
        )
        kept: list[str] = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(token in line for token in weather_tokens) or any(token in line for token in location_tokens):
                continue
            line = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", line)
            line = re.sub(r"(?:家里|学校|工作地点|外面|路上)[（(][^）)]{1,40}[）)]", r"", line)
            if line.strip():
                kept.append(line.strip())
        cleaned = "\n".join(kept).strip()
        cleaned = re.sub(r"天气[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"(?:当前位置|当前地点|所在地|所在城市|住处|住址|地址)[^。！？\n]{0,80}[。！？]?", "", cleaned)
        cleaned = re.sub(r"身处(?:家里|学校|工作地点|外面|路上)[，,；;、]?", "", cleaned)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    def _current_time_period_label(self, now: datetime | None = None) -> tuple[str, str]:
        current = now or self._environment_now()
        minute = current.hour * 60 + current.minute
        periods = [
            (0, 5 * 60, "深夜", "除非已有失眠或夜聊上下文,不要显得精神过满。"),
            (5 * 60, 7 * 60 + 30, "清晨", "适合很轻的醒来感,不要写成已经忙完一上午。"),
            (7 * 60 + 30, 10 * 60 + 30, "早晨", "可以有起床、出门、刚开始一天的余味。"),
            (10 * 60 + 30, 11 * 60 + 45, "上午后段", "还不是午休,不要提前写成吃午饭或午睡。"),
            (11 * 60 + 45, 13 * 60 + 30, "中午", "可以有吃东西、犯困、午间松下来,不要写成刚起床。"),
            (13 * 60 + 30, 17 * 60 + 30, "下午", "适合课间、工作间隙、犯困或缓慢推进。"),
            (17 * 60 + 30, 19 * 60 + 30, "傍晚", "适合收尾、路上、回家、天色变暗的生活感。"),
            (19 * 60 + 30, 22 * 60 + 30, "晚上", "适合放慢、写作业、休息或一点点夜里的黏人感。"),
            (22 * 60 + 30, 24 * 60, "深夜前段", "适合安静收声,不要写成白天刚开始。"),
        ]
        for start, end, label, guard in periods:
            if start <= minute < end:
                return label, guard
        return "当前时段", "贴着当前时间开口,不要跳到明显不属于此刻的生活场景。"

    def _format_time_period_injection(self) -> str:
        current = self._environment_now()
        label, guard = self._current_time_period_label(current)
        weekday = "一二三四五六日"[current.weekday()]
        return (
            f"当前时间：{current.strftime('%Y-%m-%d %H:%M')}（周{weekday}，{label}）。\n"
            f"使用方式：这只用于判断生活节奏和措辞,不要主动报时、报日期或解释时段。\n"
            f"时段边界：{guard}"
        )

    def _format_proactive_relationship_fact(self, user: dict[str, Any]) -> str:
        role = self._private_user_role(user) if isinstance(user, dict) else "owner"
        labeler = getattr(self, "_private_user_role_label", None)
        label = labeler(role) if callable(labeler) else ("主要用户" if role == "owner" else "次要用户")
        profile = self._relationship_profile(user if isinstance(user, dict) else {})
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        expression: dict[str, Any] = {}
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user if isinstance(user, dict) else {},
                    proactive_candidate={"eligible": True, "daily_allowance": 1},
                    message_intent={"requested_content_tier": "normal"},
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
            except Exception:
                expression = {}
        note = _single_line(user.get("proactive_boundary_note"), 80) if isinstance(user, dict) else ""
        parts: list[str] = []
        if expression:
            parts.append(
                f"统一表达决策：角色={label}，"
                f"长期阶段={_single_line(profile.get('stage_label'), 20) or '初识'}，"
                f"档位={_single_line(expression.get('expression_band'), 20) or 'relaxed'}，"
                f"语气={_single_line(expression.get('tone'), 20) or 'steady'}，"
                f"节奏={_single_line(expression.get('pacing'), 16) or 'steady'}，"
                f"直接度={_single_line(expression.get('directness'), 16) or 'natural'}，"
                f"回应={_single_line(expression.get('validation_style'), 20) or 'none'}，"
                f"自述={_single_line(expression.get('self_disclosure'), 16) or 'none'}，"
                f"幽默={_single_line(expression.get('humor_mode'), 16) or 'off'}，"
                f"话题={_single_line(expression.get('topic_initiative'), 20) or 'reply_only'}，"
                f"追问={'允许' if expression.get('followup') else '关闭'}，"
                f"当前硬额度={_safe_int(expression.get('proactive_budget'), 0, 0)}，"
                f"阶段柔性目标={_safe_int(expression.get('proactive_target'), 0, 0)}；"
                "柔性目标只用于调节频率和打扰感，不要求凑满，也不在达到后机械停发"
            )
        else:
            parts.append(f"统一表达决策不可用：角色={label}，使用低压日常表达")
        if note:
            parts.append(f"用户级备注：{note}")
        relationship_fact = "；".join(parts)
        exclusive_formatter = getattr(self, "_format_owner_exclusive_relationship_prompt", None)
        exclusive_context = ""
        if callable(exclusive_formatter) and isinstance(user, dict):
            try:
                exclusive_context = exclusive_formatter(
                    user,
                    stable_user_id=_single_line(user.get("user_id"), 160),
                    channel_scope="private",
                )
            except Exception:
                exclusive_context = ""
        return "\n\n".join(part for part in (relationship_fact, exclusive_context) if part)

    def _format_proactive_relationship_initiative_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        action: str = "message",
    ) -> PromptSection | None:
        """Offer high-affinity relational initiative without making it a quota."""
        if not isinstance(user, dict) or _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            return None
        allowed_reasons = {
            "activity_share",
            "background_schedule",
            "check_in",
            "diary_share",
            "evening_greeting",
            "quiet_care",
            "state_share",
        }
        if _single_line(reason, 48) not in allowed_reasons:
            return None
        normalized_action = _single_line(action, 80).lower()
        if not normalized_action or any(token in normalized_action for token in ("photo", "image", "screen")):
            return None
        profile_getter = getattr(self, "_relationship_profile", None)
        if not callable(profile_getter):
            return None
        try:
            profile = profile_getter(user)
        except Exception:
            return None
        if not isinstance(profile, dict):
            return None
        stage_key = _single_line(profile.get("stage_key"), 32).lower()
        if stage_key not in {"close", "intimate", "deeply_bonded"}:
            return None
        lines = [
            "- 如果本轮原本就是想靠近、关系试探或想听对方的声音，可以把泛泛关心变成一次具体而真诚的好奇：好奇用户自己的想法、对彼此相处的感受，或想一起形成的小默契。一次只选一个，不盘问，也不抢过明确的事实话题。",
            "- 这种好奇是偶尔出现的关系表达，不是每条主动消息的固定任务；不要试探用户是否喜欢你，不索取表态、秘密、承诺、排他性或即时回复。",
        ]
        if stage_key in {"intimate", "deeply_bonded"}:
            lines.append(
                "- 也可以自然请用户帮一个低负担、很具体且容易拒绝的小忙，例如给一个选择或意见、推荐一首歌、替某样东西取名，或拍一张指定主题但不敏感的生活照片。请求要来自当前话题或角色自己的真实愿望，不能只是随意给用户派任务。"
                "照片可以是此刻看到的天空、手边物件、食物或环境一角；不要索取人脸、身体私密部位、证件票据、屏幕聊天、门牌住址、实时定位或他人隐私。不能命令、查岗、要求证明感情，也不能把拒绝或没回复写成关系受损。"
            )
        return prompt_section(
            key="proactive.relationship_initiative",
            title="高亲密关系主动性",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _format_proactive_relationship_initiative_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str = "",
        action: str = "message",
    ) -> str:
        section = self._format_proactive_relationship_initiative_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _normalize_proactive_address_token(value: Any) -> str:
        token = _single_line(value, 24).strip(" -*`_【】[]（）()<>《》\"'“”‘’")
        token = re.sub(r"^[：:]+|[：:，,。.!！?？~～…]+$", "", token).strip()
        return token
