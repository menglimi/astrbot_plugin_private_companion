# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPrivatePassivePromptPart02Mixin。

由 tools/split_mixin_domain.py 从 main_private_passive_prompt.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 469 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPrivatePassivePromptMixin）。
"""
from __future__ import annotations
from .main_private_passive_prompt_shared import Any
from .main_private_passive_prompt_shared import Plain
from .main_private_passive_prompt_shared import PromptRenderMode
from .main_private_passive_prompt_shared import PromptSection
from .main_private_passive_prompt_shared import _now_ts
from .main_private_passive_prompt_shared import _safe_float
from .main_private_passive_prompt_shared import _safe_int
from .main_private_passive_prompt_shared import _single_line
from .main_private_passive_prompt_shared import _today_key
from .main_private_passive_prompt_shared import flatten_component_chunks
from .main_private_passive_prompt_shared import prompt_section
from .main_private_passive_prompt_shared import re
from .main_private_passive_prompt_shared import render_prompt_sections



class PrivateCompanionPluginPrivatePassivePromptPart02Mixin:
    """PrivateCompanionPluginPrivatePassivePromptPart02Mixin（从 PrivateCompanionPluginPrivatePassivePromptMixin 拆出）。"""


    def _format_private_routine_check_boundary_section(
        self,
        text: str,
    ) -> PromptSection:
        body = ""
        if self._is_private_routine_check_invocation(text):
            body = (
                "用户正在发起一次例行检查，但这不等于要求你自动展开固定健康清单。\n"
                "优先承接当前原始对话或可靠记忆中已经明确的双方约定；整次回复最多两个短句、最多提出一个问题。\n"
                "开头若有语气词和称呼，要和后面的承接正文自然写在同一句里，不要把“嗯，某某”“唔，某某大人”单独拆成一条消息。\n"
                "只询问当前消息、最近原始对话、明确提醒/便签或可靠记忆实际支持的项目。没有依据时，不要假定用户正在服药、生病、没吃饭或遗漏了某项现实任务。\n"
                "如果没有明确检查项目，就自然问今天想先检查哪一项；不要一口气连续追问晚饭、吃药和睡觉。"
            )
        return prompt_section(
            key="turn.routine_check_boundary",
            title="轻量例行检查边界",
            source="conversation",
            content=body,
        )

    def _limit_private_routine_check_segments(self, text: str, chunks: list[list[Any]]) -> list[list[Any]]:
        if not self._is_private_routine_check_invocation(text):
            return chunks
        limited = list(chunks or [])
        if len(limited) >= 2 and all(
            part and all(isinstance(component, Plain) for component in part)
            for part in limited[:2]
        ):
            lead = "".join(str(getattr(component, "text", "") or "") for component in limited[0]).strip()
            following = "".join(str(getattr(component, "text", "") or "") for component in limited[1]).strip()
            match = re.fullmatch(
                r"(唔|嗯|哦|啊|诶|欸|哎|唉)([\s，,、…~～]+)([\u4e00-\u9fffA-Za-z0-9·]{1,10})[\s，。！？!?,.、…~～]*",
                lead,
            )
            address = match.group(3) if match else ""
            address_titles = ("大人", "主人", "老师", "先生", "小姐", "同学", "哥哥", "姐姐", "前辈", "殿下")
            non_address_phrases = ("知道", "明白", "收到", "可以", "没事", "不用", "不要", "好了", "好吧")
            looks_like_address = bool(
                address
                and (
                    address.endswith(address_titles)
                    or (len(address) <= 4 and not any(token in address for token in non_address_phrases))
                )
            )
            if looks_like_address and following:
                separator = "" if re.search(r"[，,。！？!?、…~～]$", lead) else "，"
                limited = [[Plain(f"{lead}{separator}{following}")], *limited[2:]]
        if len(limited) <= 2:
            return limited
        return [limited[0], flatten_component_chunks(limited[1:])]

    def _private_passive_schedule_material(
        self,
        current_user: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Return evidence-backed and clock-only schedule material separately."""

        plan = self.data.get("daily_plan", {})
        if not isinstance(plan, dict):
            return "", ""

        def format_item(item: Any, *, clock_projection: bool = False) -> str:
            if not isinstance(item, dict):
                return ""
            if clock_projection:
                start = _single_line(item.get("time"), 12)
                end = _single_line(item.get("end"), 12)
                window = f"{start}-{end}" if start and end else start
                activity = _single_line(item.get("activity") or item.get("title"), 120)
                mood = _single_line(item.get("mood"), 32)
                text = "｜".join(
                    part
                    for part in (
                        window,
                        activity,
                        f"情绪：{mood}" if mood else "",
                    )
                    if part
                )
            else:
                text = self._format_plan_item_for_prompt(item)
            return self._sanitize_schedule_context_for_private_user(
                text,
                current_user or {},
            )

        current_item = self._get_current_plan_item(plan)
        verified_schedule = format_item(current_item)
        clock_item = None
        clock_getter = getattr(self, "_get_clock_plan_item_for_display", None)
        if callable(clock_getter):
            try:
                clock_item = clock_getter(plan)
            except Exception:
                clock_item = None
        if isinstance(clock_item, dict):
            lifecycle = self._normalize_schedule_lifecycle_status(
                clock_item.get("lifecycle_status") or clock_item.get("status")
            )
            if lifecycle not in {"", "planned", "active"}:
                clock_item = None
        return verified_schedule, format_item(clock_item, clock_projection=True)

    def _private_passive_state_fingerprint(self, state: dict[str, Any], current_user: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        verified_schedule, planned_schedule = self._private_passive_schedule_material(current_user)
        detail = self._current_detail_segment_for_update()
        detail_key = _single_line(detail.get("key"), 80) if isinstance(detail, dict) else ""
        detail_snapshot_getter = getattr(self, "_current_detail_snapshot_for_update", None)
        detail_snapshot = detail_snapshot_getter() if callable(detail_snapshot_getter) else None
        detail_summary = _single_line(detail_snapshot.get("summary"), 80) if isinstance(detail_snapshot, dict) else ""
        if detail_summary:
            detail_summary = self._sanitize_schedule_context_for_private_user(
                detail_summary,
                current_user or {},
            )
        friend_user = self._private_user_role(current_user or {}) == "friend"
        weather = "" if friend_user else _single_line(state.get("weather"), 60)
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        cycle_profile = self._active_body_cycle_profile(state)
        return {
            "date": _today_key(),
            "time_label": time_label,
            "energy_bracket": (energy // 10) * 10,
            "mood": _single_line(state.get("mood_bias"), 18) or "平稳",
            "activity": _single_line(verified_schedule, 100),
            "planned_activity": _single_line(planned_schedule, 100),
            "detail": f"{detail_key}|{detail_summary}" if detail_summary else detail_key,
            "weather": weather if weather and weather != "暂无天气信息" else "",
            "conditions": conditions[:2],
            "body_cycle": _single_line(state.get("body_cycle"), 120) if cycle_profile else "",
            "body_cycle_phase": _single_line(cycle_profile.get("phase"), 24),
        }

    def _format_private_passive_state_snapshot(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
    ) -> str:
        section = self._format_private_passive_state_snapshot_section(
            state,
            current_user,
            direct=direct,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_private_passive_state_snapshot_section(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        *,
        direct: bool = False,
    ) -> PromptSection:
        energy = _safe_int(state.get("energy"), 70, 0, 100)
        mood = _single_line(state.get("mood_bias"), 18) or "平稳"
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时间节奏：{time_label}", f"精神约 {energy}/100", f"情绪底色偏{mood}"]
        realtime_formatter = getattr(self, "_format_external_realtime_prompt_section", None)
        realtime_section = realtime_formatter(current_user, public=False) if callable(realtime_formatter) else None
        realtime_context = (
            render_prompt_sections(
                [realtime_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(realtime_section, PromptSection)
            else ""
        )
        verified_schedule, planned_schedule = self._private_passive_schedule_material(current_user)
        if verified_schedule and not realtime_context:
            pieces.append(f"拟人化日程素材：{verified_schedule}")
        elif verified_schedule:
            pieces.append(f"原定日程素材（已被实时共同活动覆盖）：{verified_schedule}")
        elif planned_schedule and not realtime_context:
            pieces.append(f"当前计划时段（未确认执行）：{planned_schedule}")
        elif planned_schedule:
            pieces.append(f"原定计划时段（未确认执行，已被实时共同活动覆盖）：{planned_schedule}")
        detail_snapshot_getter = getattr(self, "_current_detail_snapshot_for_update", None)
        detail_snapshot = detail_snapshot_getter() if callable(detail_snapshot_getter) else None
        if isinstance(detail_snapshot, dict):
            summary = _single_line(detail_snapshot.get("summary"), 90)
            if summary:
                summary = self._sanitize_schedule_context_for_private_user(
                    summary,
                    current_user or {},
                )
            if summary and not realtime_context:
                pieces.append(f"模拟氛围（计划细化，未确认执行）：{summary}")
            elif summary:
                pieces.append(
                    f"原定模拟氛围（计划细化，未确认执行，已被实时共同活动覆盖）：{summary}"
                )
        weather = _single_line(state.get("weather"), 60)
        if self._private_user_role(current_user or {}) == "friend":
            weather = ""
        if weather and weather != "暂无天气信息":
            pieces.append(f"天气素材：{weather}")
        conditions: list[str] = []
        raw_conditions = state.get("conditions")
        if isinstance(raw_conditions, list):
            for cond in raw_conditions[:3]:
                if not isinstance(cond, dict) or not self._should_show_condition(cond):
                    continue
                label = _single_line(cond.get("label") or cond.get("title") or cond.get("kind"), 18)
                if label and label not in conditions:
                    conditions.append(label)
        if conditions:
            pieces.append("身体感素材：" + "、".join(conditions[:2]))
        cycle_profile = self._active_body_cycle_profile(state)
        if cycle_profile:
            pieces.append(f"周期状态：Bot 当前{cycle_profile['stage']}")
        usage = (
            "本轮状态素材触发原因：用户直接询问 Bot 近况或状态。"
            if direct
            else "本轮状态素材触发原因：Bot 自身模拟状态发生变化。"
        )
        guidance = (
            "用户正在直接问 Bot 此刻在做什么或当前状态：先回答实时共同活动（若有），它高于固定日程、旧对话、旧记忆和临场发挥。"
            "固定日程只是原计划，若与实时共同活动冲突，必须说原计划被打断/覆盖，禁止继续声称仍在旧地点或旧动作中。"
            "若没有实时共同活动且有拟人化日程素材，先正面回答拟人化日程素材中的当前活动。"
            "若只有‘当前计划时段（未确认执行）’，必须用‘按计划/原本安排’口径回答，不得声称已经在执行。"
            "不得另编素材未提供的动作、地点、饮食或娱乐活动。"
            "如果素材本身较笼统，就按原有粒度自然转述，例如只说正在专心处理手头的事；不要为了显得具体而补造细节。"
            if direct
            else "只用于语气、长短、节奏和轻微接话；不要把它改写成用户做过的事或现实已经发生的事件。"
        )
        blocks = [
            "以下只描述 Bot 的拟人化内部状态/场景素材，不是用户事实、不是现实证据，也不要写入长期记忆。",
        ]
        if realtime_context:
            blocks.append(realtime_context)
        blocks.extend([
            guidance,
            usage + " " + "；".join(pieces) + "。",
        ])
        return prompt_section(
            key="state.session_update",
            title="Bot 自身模拟状态更新",
            source="daily_state",
            content="\n".join(blocks),
        )

    def _format_external_realtime_context_for_prompt(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> str:
        section = self._format_external_realtime_prompt_section(
            current_user,
            public=public,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_external_realtime_context_body(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> str:
        """Format extension state for ordinary private/group prompts.

        Active shared activity is authoritative over the schedule. Continuity is
        deliberately bounded and public views never include call transcript.
        """
        now = _now_ts()
        user = current_user if isinstance(current_user, dict) else {}
        user_id = _single_line(user.get("user_id"), 80)
        role = _single_line(user.get("relationship_role"), 24)
        role_getter = getattr(self, "_private_user_role", None)
        if callable(role_getter):
            try:
                role = _single_line(role_getter(user, user_id), 24)
            except TypeError:
                role = _single_line(role_getter(user), 24)
            except Exception:
                pass
        activity: dict[str, Any] = {}
        registry = getattr(self, "_external_realtime_activities", None)
        if isinstance(registry, dict):
            for key, item in list(registry.items()):
                if not isinstance(item, dict) or _safe_float(item.get("expires_at"), 0.0) <= now:
                    registry.pop(key, None)
                    continue
                item_user = _single_line(item.get("user_id"), 80)
                if item_user and item_user == user_id:
                    activity = dict(item)
                    break
                if not activity and (public or role != "owner"):
                    activity = dict(item)
        continuity: dict[str, Any] = {}
        continuity_registry = getattr(self, "_external_realtime_continuity", None)
        if isinstance(continuity_registry, dict) and user_id:
            item = continuity_registry.get(user_id)
            if isinstance(item, dict) and _safe_float(item.get("expires_at"), 0.0) > now:
                continuity = dict(item)
        if not activity and not continuity:
            return ""
        label = _single_line(activity.get("label"), 140) or {
            "shared_call": "正在和主要用户通话",
            "shared_watch": "正在和主要用户一起看视频",
            "shared_work": "正在和主要用户进行工作协同",
        }.get(_single_line(activity.get("kind"), 40), "正在进行共同活动")
        if public:
            return (
                f"{label}。这是当前优先级最高的实时事实，固定日程只是原计划。"
                "群聊只可概括说明正在与主要用户共同活动，不得透露电话内容、具体约定、逐字转写或私密地点。"
            )
        lines = [
            "实时共同活动是正在发生的高优先级事实；固定日程只是原计划，冲突时必须以实时活动为准。",
        ]
        if activity:
            lines.append(f"当前活动：{label}")
        summary = _single_line(continuity.get("summary"), 1800)
        if summary:
            lines.append(
                "最近通话/共同活动的短期连续性（带说话人归属，仅作自然接续，不是长期记忆）："
                + summary
            )
        return "\n".join(lines)

    def _private_passive_state_reply_policy_prompt(self) -> str:
        section = self._private_passive_state_reply_policy_section()
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _private_passive_state_reply_policy_section(
        self,
        *,
        compact: bool = False,
    ) -> PromptSection:
        lines = (
            [
                "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态；不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。"
            ]
            if compact
            else [
                "先自然回应用户当前表达；主动提供一处与 Bot 自身有关的具体细节；不要逐项汇报状态，也不要把内部素材描述成已经证实的现实事件。",
                "不要把回复写成连续盘问；整次回复最多提出一个问题；没有必要时可以不提问。",
                "当前用户最后一条消息是本轮唯一的主线：先接住其中的具体词、问题或情绪，再决定是否补充背景。旧话题、未完成话头和状态素材只有在与当前内容有明确语义连接时才轻轻带过；不贴合就留在背景里，不要为了连续性硬拽回来。",
                "话题确实转向时，用当前消息里的连接点自然过渡，不要凭空写“刚刚/刚才/前面”作为转场。相对时间词只在用户明确提到时间、或有可靠事实表明确实发生在那个时间段时使用；内部提示中的时间标签不得原样出现在回复里。",
            ]
        )
        return prompt_section(
            key="state.reply_policy",
            title="私聊被动回复策略",
            source="daily_state",
            content="\n".join(lines),
        )

    def _format_private_passive_state_continuity_anchor(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
    ) -> str:
        section = self._format_private_passive_state_continuity_anchor_section(
            state,
            current_user,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_private_passive_state_continuity_anchor_section(
        self,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
    ) -> PromptSection:
        now = self._environment_now()
        time_label, _ = self._current_time_period_label(now)
        pieces = [f"时段={time_label}"]

        raw_energy = state.get("energy") if isinstance(state, dict) else None
        if isinstance(raw_energy, (int, float)) and not isinstance(raw_energy, bool):
            energy = _safe_int(raw_energy, 70, 0, 100)
            energy_floor = min(90, (energy // 10) * 10)
            energy_ceiling = 100 if energy_floor == 90 else energy_floor + 9
            pieces.append(f"精力={energy_floor}-{energy_ceiling}/100")

        mood = (
            _single_line(state.get("mood_bias"), 18) if isinstance(state, dict) else ""
        )
        if mood:
            pieces.append(f"情绪底色={mood}")

        current_item = self._get_current_plan_item(self.data.get("daily_plan", {}))
        activity = ""
        scene_text = ""
        if isinstance(current_item, dict):
            scene_text = self._sanitize_schedule_model_artifacts(
                current_item.get("activity"), limit=72
            )
            future_marker = re.search(
                r"准备\s*(?:(?:先|再|马上|即将|随后|然后|接着|待会儿?|等会儿?|晚点|稍后)\s*)?"
                r"(?:去|到|回|前往|出发|开始|继续|做|处理|整理|收拾|上课|自习|洗漱|洗澡|睡觉|"
                r"出门|吃饭|用餐|跑步|散步|运动|锻炼|看书|读书|写作|买东西|买菜)|"
                r"正要|马上|即将|稍后|之后|随后|然后|接着|待会儿?|等会儿?|过(?:一)?会儿|一会儿后|"
                r"晚点|晚些时候|接下来|下一段|再(?:去|到|回|前往|开始|继续|做|处理|整理|收拾)|"
                r"(?:做|整理|收拾|写|看|读|处理)?完(?:后)?(?:再)?(?:去|到|回|前往)",
                scene_text,
            )
            if future_marker:
                scene_text = scene_text[: future_marker.start()].rstrip(" ，,；;。")
            if scene_text and self._daily_plan_clause_has_unsafe_social_fact(
                scene_text
            ):
                scene_text = ""
            if scene_text and re.search(
                r"用户|主要用户|当前用户|主人|对方|给你|和你|跟你|你在|你的|明天|后天|下周|未来|日程|计划|打算|将要",
                scene_text,
            ):
                scene_text = ""
            scene_text = self._sanitize_schedule_context_for_private_user(
                scene_text, current_user or {}
            )
            if scene_text and re.search(
                r"(?:^|[，,；;。])(?:准备|正要|要去|想去|去往|前往|出发|赶往|回到?)",
                scene_text,
            ):
                scene_text = ""
            action_match = re.search(
                r"(?:整理|收拾|看书|阅读|读书|写作|写字|写笔记|听歌|听音乐|休息|发呆|学习|"
                r"上课|自习|工作|处理|做饭|吃饭|用餐|洗漱|洗澡|睡觉|散步|运动|锻炼|画画|"
                r"练习|聊天|看电影|看视频|玩游戏|刷手机|喝咖啡|喝茶|做手工|晒太阳|通勤|买东西|买菜)"
                r"[^，,；;。]{0,52}",
                scene_text,
            )
            if action_match:
                activity = action_match.group(0).strip()
                if re.search(
                    r"(?:在|到|去|回|靠近|路过|位于|身处)[^，,；;。]{1,24}|"
                    r"[^，,；;。]{2,24}(?:省|市|区|县|镇|村|路|街|巷|号|小区|校区|商场|广场|"
                    r"大厦|园区|车站|机场|酒店|咖啡店|餐厅|公园|图书馆)",
                    activity,
                ):
                    activity = ""
        if activity:
            pieces.append(f"当前活动={_single_line(activity, 56)}")
        if scene_text:
            inferred_location = self._coarse_roleplay_location_text(
                self._infer_location_from_text(scene_text)
            )
            safe_location = self._sanitize_schedule_context_for_private_user(
                f"当前位置：{inferred_location}" if inferred_location else "",
                current_user or {},
            )
            if safe_location:
                pieces.append(f"粗略位置={inferred_location}")

        lines = [
            "这是 Bot 的拟人化模拟状态，不是用户事实、现实证据或长期记忆。",
            "当下素材（仅供隐性承接）：" + "；".join(pieces) + "。",
        ]
        return prompt_section(
            key="state.session_update",
            title="Bot 当下连续性",
            source="daily_state",
            content="\n".join(lines)[:300],
        )
