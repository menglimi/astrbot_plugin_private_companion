# -*- coding: utf-8 -*-
"""ProactiveMessageFrameworkPromptPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_framework_prompt.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 550 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageFrameworkPromptMixin）。
"""
from __future__ import annotations

from .proactive_message_framework_prompt_shared import _now_ts, logger
from .proactive_message_framework_prompt_shared import Any
from .proactive_message_framework_prompt_shared import PromptDocument
from .proactive_message_framework_prompt_shared import PromptLabelStyle
from .proactive_message_framework_prompt_shared import PromptRenderMode
from .proactive_message_framework_prompt_shared import PromptSection
from .proactive_message_framework_prompt_shared import ProviderRequest
from .proactive_message_framework_prompt_shared import _CapturedFrameworkSendMessage
from .proactive_message_framework_prompt_shared import _CapturedSendMessageCall
from .proactive_message_framework_prompt_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_framework_prompt_shared import _proactive_prompt_part
from .proactive_message_framework_prompt_shared import _safe_float
from .proactive_message_framework_prompt_shared import _safe_int
from .proactive_message_framework_prompt_shared import _single_line
from .proactive_message_framework_prompt_shared import prompt_document
from .proactive_message_framework_prompt_shared import prompt_section
from .proactive_message_framework_prompt_shared import re
from .proactive_message_framework_prompt_shared import render_prompt_document
from .proactive_message_framework_prompt_shared import render_prompt_sections
from .proactive_message_framework_prompt_shared import runtime_persona_setting



class ProactiveMessageFrameworkPromptPart02Mixin:
    """ProactiveMessageFrameworkPromptPart02Mixin（从 ProactiveMessageFrameworkPromptMixin 拆出）。"""


    def _proactive_llm_segmenting_allowed(self, *, umo: str = "") -> bool:
        if not bool(runtime_persona_setting(self, "enable_segmented_proactive_reply", False)):
            return False
        if not bool(runtime_persona_setting(self, "enable_llm_controlled_segmenting", False)):
            return False
        scope_checker = getattr(self, "_segmented_scope_allows_umo", None)
        try:
            if callable(scope_checker) and not bool(scope_checker(umo)):
                return False
        except Exception:
            return False
        platform_checker = getattr(self, "_segmented_platform_allows", None)
        try:
            if callable(platform_checker) and not bool(platform_checker(umo=umo)):
                return False
        except Exception:
            return False
        return True

    def _proactive_llm_segmenting_instruction(self, *, umo: str = "") -> str:
        """Return the marker contract only for user-visible proactive text."""
        if not self._proactive_llm_segmenting_allowed(umo=umo):
            return ""
        section_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
        if not callable(section_getter):
            return ""
        section = section_getter()
        if not isinstance(section, PromptSection):
            return ""
        return render_prompt_sections([section])

    @staticmethod
    def _proactive_visible_text_format_prompt_section(action: str) -> PromptSection:
        action_name = _single_line(action, 80) or "message"
        return prompt_section(
            key="proactive.visible_text_format",
            title="主动可见正文格式",
            source="proactive_message",
            content=(
                f"- 当前动作：{action_name}。这里生成的是最终显示在聊天里的普通正文；图片动作写可见附言，语音动作的朗读内容和音频会由独立链路生成。\n"
                "- 人格中的 TTS 专用规则只约束独立语音脚本，不约束这里的可见正文。不要输出 <tts>/<pc_tts>、[happy]/[sad] 等情绪控制词、语音专用日语或外语朗读稿、音标，也不要把语音内容再作为文字重复发送。\n"
                "- 可见正文继续遵守人格平时的聊天语言和口吻；只有当人格本身明确要求日常可见聊天使用某种语言时，才使用该语言，不能仅凭 TTS 语种要求切换。"
            ),
        )

    @classmethod
    def _proactive_visible_text_format_hint(cls, action: str) -> str:
        return render_prompt_sections(
            [cls._proactive_visible_text_format_prompt_section(action)],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_proactive_generation_intent_prompt_section(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_context: str = "",
    ) -> PromptSection | None:
        semantics: dict[str, Any] = {}
        semantic_getter = getattr(self, "_planned_proactive_semantics", None)
        if callable(semantic_getter):
            try:
                semantics = semantic_getter(user)
            except Exception as exc:
                logger.debug("主动生成语义提示读取失败: %s", _single_line(exc, 120))
                semantics = {}
        readiness: dict[str, Any] = {}
        readiness_getter = getattr(self, "_proactive_inner_readiness", None)
        if callable(readiness_getter):
            try:
                readiness = readiness_getter(user)
            except Exception as exc:
                logger.debug("主动生成内在状态提示读取失败: %s", _single_line(exc, 120))
                readiness = {}

        kind = _single_line(semantics.get("kind"), 40)
        anchor_type = _single_line(semantics.get("anchor_type"), 40)
        semantic_score = _safe_float(semantics.get("score"), 0.5)
        semantic_pressure = _safe_float(semantics.get("pressure"), 0.4)
        semantic_risk = _safe_float(semantics.get("risk"), 0.0)
        semantic_note = _single_line(semantics.get("note"), 140)
        readiness_label = _single_line(readiness.get("label"), 60)
        readiness_score = _safe_float(readiness.get("score"), 0.55)
        temperature = readiness.get("temperature") if isinstance(readiness.get("temperature"), dict) else {}
        temperature_label = _single_line(temperature.get("label"), 60)
        temperature_score = _safe_float(temperature.get("score"), 0.55)
        motivation = readiness.get("motivation") if isinstance(readiness.get("motivation"), dict) else {}
        expression_decision = readiness.get("expression_decision") if isinstance(readiness.get("expression_decision"), dict) else {}

        lines: list[str] = []
        closing_getter = getattr(self, "_proactive_conversation_closing_until", None)
        current_now = _now_ts()
        closing_until = 0.0
        if callable(closing_getter):
            try:
                closing_until = _safe_float(closing_getter(user, now=current_now), 0.0)
            except Exception as exc:
                logger.debug("对话收束状态读取失败: %s", _single_line(exc, 120))
        if closing_until > current_now:
            remaining_minutes = max(1, int((closing_until - current_now) / 60))
            lines.append(
                f"上一条主动已完成一次对话收束，短暂留白还剩约 {remaining_minutes} 分钟。"
                "这是对话节奏状态，不是用户休息或永久禁言：普通候选不要借机重新开话题；"
                "只有本轮已有明确新来源、用户新消息或有时效的事项才可自然承接。"
                "若本轮仍需生成，正文保持短、完整、低压力，不解释这条内部状态。"
            )
        troubleshooting_hint = self._proactive_troubleshooting_request_hint(user)
        if troubleshooting_hint:
            lines.append(troubleshooting_hint)
        if kind or anchor_type:
            lines.append(
                f"候选语义：{kind or 'check_in'}/{anchor_type or 'vague'}；"
                f"自然度 {semantic_score:.2f}，打扰压力 {semantic_pressure:.2f}，风险 {semantic_risk:.2f}。"
                + (f" 备注：{semantic_note}。" if semantic_note else "")
            )
        if readiness_label or temperature_label:
            lines.append(
                f"开口欲：{readiness_label or '平稳'} {readiness_score:.2f}；"
                f"主动表达温度：{temperature_label or '平稳'} {temperature_score:.2f}。"
            )
        if motivation:
            lines.append(
                f"实验动机调度：{_single_line(motivation.get('label'), 24)} "
                f"{_safe_float(motivation.get('score'), 0.5):.2f}；"
                f"{_single_line(motivation.get('detail'), 120)}。"
            )
        if expression_decision:
            lines.append(
                "统一表达："
                f"档位={_single_line(expression_decision.get('expression_band'), 24) or 'relaxed'}；"
                f"语气={_single_line(expression_decision.get('tone'), 24) or 'steady'}；"
                f"距离={_single_line(expression_decision.get('address_style'), 24) or 'neutral'}；"
                f"节奏={_single_line(expression_decision.get('pacing'), 16) or 'steady'}；"
                f"直接度={_single_line(expression_decision.get('directness'), 16) or 'natural'}；"
                f"回应={_single_line(expression_decision.get('validation_style'), 20) or 'none'}；"
                f"自述={_single_line(expression_decision.get('self_disclosure'), 16) or 'none'}；"
                f"幽默={_single_line(expression_decision.get('humor_mode'), 16) or 'off'}；"
                f"话题={_single_line(expression_decision.get('topic_initiative'), 20) or 'reply_only'}；"
                f"追问={'允许' if expression_decision.get('followup') else '关闭'}；"
                f"当前硬额度={_safe_int(expression_decision.get('proactive_budget'), 0, 0)}；"
                f"阶段柔性目标={_safe_int(expression_decision.get('proactive_target'), 0, 0)}；"
                "结合真实由头、对方反馈和打扰感自然调整，不要求凑满或机械卡线；"
                "内容尺度=normal。"
            )
        relationship_initiative_hint = self._format_proactive_relationship_initiative_hint(
            user,
            reason=reason,
            action=action,
        )
        if relationship_initiative_hint:
            lines.append(relationship_initiative_hint)
        model_judgement = (
            user.get("planned_proactive_model_judge_result")
            if isinstance(user.get("planned_proactive_model_judge_result"), dict)
            else {}
        )
        model_note = _single_line(model_judgement.get("reason"), 140)
        if model_note and any(token in model_note for token in ("软质量建议", "收敛", "改写", "偏低", "偏虚", "不自然")):
            lines.append(
                f"人格计划判定的表达建议：{model_note}。"
                "这只是正文改写方向，不是取消理由；保持原计划事实边界，直接修成自然、具体、低压力的一两句。"
            )
        afterglow = user.get("proactive_afterglow") if isinstance(user.get("proactive_afterglow"), dict) else {}
        if afterglow:
            afterglow_age = _now_ts() - _safe_float(afterglow.get("ts"), 0)
            if 0 <= afterglow_age <= 48 * 3600:
                afterglow_label = _single_line(afterglow.get("label"), 120)
                afterglow_tendency = _single_line(afterglow.get("next_tendency"), 140)
                afterglow_status = _single_line(afterglow.get("status"), 40)
                if afterglow_label or afterglow_tendency:
                    lines.append(
                        f"上一条主动回声：{afterglow_status or 'unknown'}｜"
                        f"{afterglow_label or '仍在等待自然落地'}；{afterglow_tendency or '下一次按关系反馈调整'}。"
                    )

        if semantic_score < 0.48 or semantic_pressure >= 0.58:
            lines.append("这次由头不算很硬或打扰压力偏高：正文要更短、更轻，最好像把一句话放下，不追问、不求回应。")
        elif semantic_score >= 0.68:
            lines.append("这次有明确由头：正文可以贴着那个由头说一个具体点，但仍然不要解释调度原因。")
        if readiness_score < 0.36 or temperature_score < 0.34:
            lines.append(
                "Bot 当前开口欲或主动表达温度偏低，这只影响写法，不是取消发送的理由："
                "用一句更安静、更短的自然话表达，不表演热情，不制造必须回应的压力。"
            )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0, 0)
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        if unanswered_count >= 2 and not (quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}):
            lines.append(
                "对方已连续多次没有回应：只保留一个完整意思，优先改写为一句自然短句；"
                "不要同时堆叠近况、提问和叮嘱；不要用‘在吗/最近忙不忙/只是想找你’作为唯一内容，"
                "优先贴着当前真实生活片段或计划里的具体点轻轻说一句；任何收短都必须保证句意完整，不能留下半句话。"
            )
        if reason not in {"environment_change", "weather_alert"}:
            lines.append("天气和气温只作环境底色，本轮不要把它们改写成正文话题，也不要顺手追问对方那边的天气；改用本轮明确动机、生活片段或最近真实话题。")
        if reason == "health_alert":
            body_health_hint_getter = getattr(self, "_format_body_monitor_health_prompt", None)
            body_health_hint = body_health_hint_getter(user, reason=reason) if callable(body_health_hint_getter) else ""
            if body_health_hint:
                lines.append(body_health_hint)
            lines.append("这是一次有时效的身体状态关心线索：只温和问候当前感受，不作医疗判断，不夸大风险，也不要求对方立即回复。")
        elif reason == "low_balance":
            balance_hint_getter = getattr(self, "_format_balance_awareness_prompt", None)
            balance_hint = balance_hint_getter(user, reason=reason) if callable(balance_hint_getter) else ""
            if balance_hint:
                lines.append(balance_hint)
            lines.append("这是用户明确开启的余额感知事件：允许按人格轻轻要零花钱或补给，但只提一次，不催促、不索要回复，也不把服务余额写成用户欠款。")
        elif reason == "environment_change":
            environment_hint_getter = getattr(self, "_format_environment_change_prompt", None)
            environment_hint = environment_hint_getter(user, reason=reason) if callable(environment_hint_getter) else ""
            if environment_hint:
                lines.append(environment_hint)
            lines.append("这是有短时效的环境变化：只贴着刚发生的变化说一个具体点，不扩写预报，不假设用户正在室外，也不解释信息来源。")
        elif reason == "weather_alert":
            weather_alert_hint_getter = getattr(self, "_format_weather_alert_prompt", None)
            weather_alert_hint = weather_alert_hint_getter(user, reason=reason) if callable(weather_alert_hint_getter) else ""
            if weather_alert_hint:
                lines.append(weather_alert_hint)
            lines.append("这是来自官方气象渠道的当前预警：优先保留等级、现象和防护建议等事实，用熟悉的口吻及时说清；不要提接口、缓存、轮询、API Host 或内部字段，不把预警写成夸张灾情，也不要替用户判断已经发生了什么。")
        elif reason == "personal_goal_progress":
            personal_goal_hint_getter = getattr(self, "_format_personal_goal_prompt", None)
            personal_goal_hint = personal_goal_hint_getter(user, reason=reason) if callable(personal_goal_hint_getter) else ""
            if personal_goal_hint:
                lines.append(personal_goal_hint)
            lines.append("这是 Bot 自己的非创作型长期目标变化：只说一个真实进展、停滞或完成结果，不向用户索取监督，不把百分比写成系统汇报。")
        elif reason == "memo_note_reminder":
            memo_hint_getter = getattr(self, "_format_memo_note_prompt", None)
            memo_hint = memo_hint_getter(user, reason=reason) if callable(memo_hint_getter) else ""
            if memo_hint:
                lines.append(memo_hint)
            lines.append("这是用户自己设置的到期便签：直接提醒便签里的事项，一次说清，不解释为什么现在发送，也不要追问用户是否完成。")
        elif reason == "morning_greeting":
            lines.append("这是当天第一次普通早安：只自然打招呼或递出一个很轻的早晨片段，说完就停。用户还没有回应，禁止问早餐/早饭、吃了吗、吃什么，也不要追加起床查岗、健康确认或其他需要回答的问题；饮食关心会在用户回应后的独立时机处理。")
        elif reason == "meal_care":
            lines.append("这是饭点关心：自然问用户这一顿吃了没有。问题主体必须是用户，不要回答成自己吃了什么；像熟悉的人顺口惦记一句，不说教、不盘问，也不要同一条里连续列很多问题。")
        elif reason == "meal_care_followup":
            lines.append("这是一次且仅一次的吃饭补问：根据话题判断是确认后来有没有吃上，还是问已经吃过的具体内容。保持很短、低压力，不责怪用户没回，也不要重复上一句原话。")
        elif reason == "birthday_eve_hint":
            lines.append("这是生日前夜的一点留白：可以温柔地提醒对方明天多偏爱自己一点，但不要说出生日、准备、惊喜或任何剧透；一小句就停，不制造期待压力。")
        elif reason == "birthday_makeup":
            lines.append("这是次日午前的低调补送：真诚祝福即可，不要反复道歉、不解释系统或错过原因，也不要把昨天的生日写成今天。")
        elif reason == "birthday_afterglow":
            lines.append("这是用户在生日祝福后已经回应过才会出现的余温收尾：只轻轻接住一个开心瞬间，不重复说生日快乐、不追问安排，也不延长成连续庆祝。")
        elif reason == "birthday_celebration":
            lines.append("今天是用户明确允许记住的生日，是一年一次的轻量仪式。表达必须服从当前人格：可以热闹、安静、含蓄或只留一句，不要强行煽情。先送出真诚、具体、低压力的祝福；不要提系统、记录、年龄、出生年份或精确日期，不承诺永远陪伴，也不要求回复或追问庆祝安排。若带图，正文只自然递出，不描述制作过程。")
        elif reason == "special_day_greeting":
            special_context = user.get("planned_special_day_context") if isinstance(user.get("planned_special_day_context"), dict) else {}
            title = _single_line(special_context.get("observance_title"), 32) or "这个特别的日子"
            timing = _single_line(special_context.get("delivery_timing"), 24)
            if timing == "midnight":
                lines.append(f"这是{title}零点后的第一句问候：先看人格是否喜欢仪式感；若不偏节日表达，就用平常口吻轻轻带过，不要硬写浪漫。只围绕一个具体情绪或祝愿自然说一句；不要写成节日科普、营销文案、固定祝福模板，也不要要求用户回复或追问安排。")
            else:
                lines.append(f"这是错过零点后的{title}白天补上：自然承认今天这个特别日子即可，不解释系统延迟，不使用僵硬的节日贺词，不把普通寒暄扩成盘问。")
        elif reason == "birthday_curiosity":
            lines.append("这是一次低频的资料好奇：只自然地问生日的月日，可顺带问公历还是农历；明确说不想回答也完全没关系。不要索要出生年份、年龄、证件信息，也不要假装已经准备了生日惊喜。")
        elif reason == "web_exploration_share":
            lines.append("自然地向用户分享自己刚看的这条内容。只把标题、探索印象和链接当作事实依据，像当前人格平时聊天一样表达。")
        elif kind in {"continuation", "reminder"}:
            lines.append("这是有来源的续接/提醒：可以顺着来源，但不要写成用户刚刚又发了新消息。")
        elif kind in {"self_share", "external_share", "observation"}:
            lines.append("这是分享/观察型主动：只取一个最小切口，不写成报告、推荐文或观察总结。")
            true_external_info = reason in {"bili_video_share", "news_share", "web_exploration_share"}
            if true_external_info:
                lines.append("外界分享必须贴住这次看到的标题、视频、新闻或资料本身；如果只是低压地放一句，也要围绕来源表达感受，不要改成无关的个人状态或泛泛压力询问。")
                lines.append("最终正文必须让用户一眼知道你在分享什么：至少带标题、BV/链接、来源名或具体内容锚点之一；不要只写“看这个/这条好离谱/给你看个东西”。")
            elif anchor_type == "group_context":
                lines.append("群聊见闻只是一段共同群里的小片段：可以轻轻转述一个具体笑点或画面，不要把内部话题名写成“标题/新闻/资料”。")
        elif kind in {"care", "check_in", "light_touch"}:
            lines.append("这是靠近型主动：不要直接说想念、关心或刷存在感，要侧着落到一个小动作或小片段。")

        hesitation_note = _single_line(user.get("last_proactive_hesitation_note"), 100)
        hesitation_at = _safe_float(user.get("last_proactive_hesitation_at"), 0)
        if hesitation_note and hesitation_at > 0 and _now_ts() - hesitation_at <= 12 * 3600:
            lines.append(f"前面有过一次犹豫：{hesitation_note}。如果要用，只能变成很淡的语气底色，不要明说系统延后。")
        deferred_share_tense_hint = self._deferred_immediate_share_tense_hint(user, action)
        if deferred_share_tense_hint:
            lines.append("这段生活分享已不是当下现场：必须使用已发生时态，不要暗示事件与发送同一时刻，也不要解释延后。")

        if _safe_int(user.get("ignored_streak"), 0, 0) > 0:
            lines.append("对方最近还没回应：不要连续提问，不要控诉，也不要把沉默写成对方故意不理。")
        if "message" == str(action or "message") and not _single_line(action_context, 120):
            lines.append("本轮没有真实媒体或工具结果：正文只围绕聊天内容本身，不描述动作结果。")
        lines.append("以上只用于决定怎么写，最终正文里不要出现“语义/自然度/压力/风险/开口欲/主动表达温度/犹豫”等分析词。")
        if len(lines) <= 1:
            return None
        return prompt_section(
            key="proactive.generation_intent",
            title="这次主动的内在约束",
            source="proactive_message",
            content="\n".join(lines),
        )

    def _format_proactive_generation_intent_hint(
        self,
        user: dict[str, Any],
        *,
        reason: str,
        action: str,
        motive: str = "",
        action_context: str = "",
    ) -> str:
        section = self._format_proactive_generation_intent_prompt_section(
            user,
            reason=reason,
            action=action,
            motive=motive,
            action_context=action_context,
        )
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    def _unexecuted_relay_claim_reason(self, text: str, *, action_context: str = "") -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        context = str(action_context or "")
        if any(token in context for token in ("pc_relay_message", "转述工具", "消息已发送", "已挂起", "atrelay")):
            return ""
        target_patterns = (
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:去|会|来|可以)?(?:帮你|替你)?(?:跟|和|给)([^，。！？!?、\s]{1,12})(?:说一声|说一下|转告|转达|带话|留言)",
            r"我(?:这就|现在|等下|一会儿|待会儿)?(?:帮你|替你)([^，。！？!?、\s]{0,12})(?:转告|转达|带话|留言)",
            r"(?:已经|已)(?:帮你|替你)?(?:转告|转达|带话|留言|说过)",
        )
        for pattern in target_patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            target = _single_line(match.group(1) if match.lastindex else "", 20)
            if target and target.startswith(("你", "妳")):
                continue
            return "没有真实转述工具执行结果"
        return ""

    def _fallback_unexecuted_relay_reply(self, inbound_text: str) -> str:
        inbound = _single_line(inbound_text, 160)
        if any(token in inbound for token in ("替我", "帮我", "你去", "跟他", "和他", "跟她", "和她", "说一声", "转告", "转达")):
            return "我不能假装已经说过。你把对象和要带的话再说清楚一点。"
        return "我不能假装已经替你说过。要我带话的话，你把对象和内容说清楚。"

    def _proactive_time_guard_hint(self, reason: str, current_item: dict[str, Any] | None) -> str:
        activity = _single_line((current_item or {}).get("activity"), 80)
        _, period_guard = self._current_time_period_label()
        prefix = f"先遵守当前真实时段：{period_guard}"
        if reason == "morning_greeting":
            return f"{prefix} 这次只能像早晨刚醒、赖床、洗漱或刚开始一天时那样开口；只做自然问候，不问早餐、吃了吗或吃什么，也不要附带健康和查岗问题。"
        if reason == "noon_greeting":
            return f"{prefix} 这次只能像中午、吃东西、发懒、午间发呆或午休前后那样开口；不要写成刚醒起床或准备睡觉。"
        if reason == "evening_greeting":
            return f"{prefix} 这次只能像傍晚收尾、天色往下落、回到家或一天快慢下来时那样开口；不要写成刚醒起床。"
        if activity and any(token in activity for token in ("便利店", "出门", "吹风", "路上", "窗边", "收拾", "吃", "洗漱", "洗澡", "刷视频", "书桌")):
            return f"{prefix} 优先贴着这一小段生活片段来开口：{activity}。不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"
        return f"{prefix} 贴着当前这小段生活片段开口，不要忽然跳成不在这个时段里的“刚醒”“赖床”或“要睡了”。"

    @staticmethod
    def _framework_voice_prompt_document(
        *,
        name: str,
        reason: str,
        last_user_message: str,
        relationship_level: str,
        relationship_preference: str,
        state_hint: str,
        busy_hint: str,
        tts_prompt: str,
        requirement_summary: str,
        strict_tts: bool,
    ) -> PromptDocument:
        rules = [
            "1. 只输出这句真正要被念出来的语音内容，不要解释。",
            "2. 如果当前人格或 TTS 规则要求使用 <tts>...</tts>、日语、情绪标签、双语格式，就严格遵守。",
            "3. 如果没有明确格式要求，就写成适合私聊语音的一小句，不像朗读稿。",
            "4. 可以有一点嘴硬、黏人、藏着的想念，但不要把喜欢说满。",
            "5. 不要提 AI、模型、插件、TTS、语音合成这些词。",
        ]
        if strict_tts:
            rules.append("6. 这次必须优先满足语音格式要求；如果有日语或 <tts> 规则，不要退回普通中文句子。")
        return prompt_document(
            user_render=_PROACTIVE_DOCUMENT_RENDER,
            user=(
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.task",
                    title="主动语音正文生成",
                    source="proactive_message",
                    content=(
                        "你现在要在同一段私聊会话里，准备一小句真正会被念出来的主动语音内容。\n"
                        "当前会话里已有的人格、关系、上下文会继续生效，这里不要再重复铺陈。\n"
                        "站位必须清楚：这是你主动发语音,不是对方刚刚来找你、叫醒你或问候你。"
                        "聊天历史只作背景,不要把最后一句历史当成当前新消息。"
                    ),
                ), mode=PromptRenderMode.BODY_ONLY),
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.context",
                    title="补充信息",
                    source="proactive_message",
                    content=(
                        f"- 对方称呼：{name}\n"
                        f"- 主动原因：{reason}\n"
                        f"- 最近一句用户消息：{last_user_message or '（暂无）'}\n"
                        f"- 关系画像：{relationship_level}｜偏好：{relationship_preference}\n"
                        f"- 当前状态底色：{state_hint or '今天整体比较平稳。'}\n"
                        f"- 忙碌表达倾向：{busy_hint or '当前没有额外的忙碌表达倾向。'}\n"
                        f"- 当前会话 TTS 规则：{tts_prompt or '（当前没有额外 TTS 提示词,就按人格自己的语音习惯来）'}\n"
                        f"- 当前语音格式重点：{requirement_summary}"
                    ),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
                _proactive_prompt_part(prompt_section(
                    key="background.voice_framework.rules",
                    title="要求",
                    source="proactive_message",
                    content="\n".join(rules),
                ), label_style=PromptLabelStyle.FULLWIDTH_COLON),
            ),
            metadata={"task": "proactive_voice"},
        )

    def _build_framework_voice_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        target: str,
        strict_tts: bool = False,
    ) -> str:
        state = self.data.get("daily_state", {})
        last_user_message = _single_line(user.get("last_user_message"), 80)
        profile = self._relationship_profile(user)
        tts_prompt = self._get_tts_prompt_text(target)
        req = self._voice_requirement_profile(target)
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action="voice",
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        busy_hint = ""
        busy_context_getter = getattr(self, "_busy_proactive_voice_context", None)
        try:
            busy_context = busy_context_getter() if callable(busy_context_getter) else {}
        except Exception:
            busy_context = {}
        if isinstance(busy_context, dict) and busy_context.get("busy"):
            busy_hint = (
                "正处于忙碌片段，像腾不出手时顺手留的一句；"
                "控制在一两句短口语，不复述日程、不报内部状态，也不要扩展成说明。"
            )
        return render_prompt_document(
            self._framework_voice_prompt_document(
                name=name,
                reason=reason,
                last_user_message=last_user_message,
                relationship_level=profile["level"],
                relationship_preference=profile["preference"],
                state_hint=state_hint,
                busy_hint=busy_hint,
                tts_prompt=tts_prompt,
                requirement_summary=req["summary"],
                strict_tts=strict_tts,
            )
        )["user"]

    async def _capture_framework_send_message_calls(
        self,
        *,
        target_session: str,
        runner_factory: Any,
        max_steps: int = 20,
    ) -> tuple[Any, list[_CapturedSendMessageCall]]:
        captured: list[_CapturedSendMessageCall] = []
        try:
            from astrbot.core.tools.message_tools import SendMessageToUserTool
            from astrbot.core.agent.runners.tool_loop_agent_runner import _ToolExecutionInterrupted
        except Exception:
            result = await runner_factory()
            return result, captured

        original_call = SendMessageToUserTool.call

        async def _intercept_call(tool_self, context, **kwargs):
            session_value = kwargs.get("session") or getattr(
                getattr(getattr(context, "context", None), "event", None),
                "unified_msg_origin",
                "",
            )
            messages = kwargs.get("messages")
            session_text = str(session_value or "")
            if session_text == target_session and isinstance(messages, list):
                captured.append(_CapturedSendMessageCall(session_text, messages))
                logger.info(
                    "已拦截框架内 send_message_to_user 工具调用: session=%s components=%s",
                    session_text,
                    len(messages),
                )
                raise _ToolExecutionInterrupted("PrivateCompanion captured send_message_to_user payload.")
            return await original_call(tool_self, context, **kwargs)

        SendMessageToUserTool.call = _intercept_call
        try:
            result = await runner_factory()
            runner = getattr(result, "agent_runner", None) if result is not None else None
            if runner is not None and hasattr(runner, "step_until_done"):
                try:
                    async for _ in runner.step_until_done(max_steps):
                        pass
                except (_CapturedFrameworkSendMessage, _ToolExecutionInterrupted):
                    logger.info(
                        "主动主链工具发送已捕获,提前结束工具循环: session=%s captured=%s",
                        target_session,
                        len(captured),
                    )
        finally:
            SendMessageToUserTool.call = original_call
        return result, captured

    def _captured_send_plain_text(self, captured_tool_sends: list[Any]) -> str:
        if not captured_tool_sends:
            return ""
        captured_text_parts: list[str] = []
        for call in captured_tool_sends:
            messages = getattr(call, "messages", [])
            if not isinstance(messages, list):
                continue
            for item in messages:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").strip().lower() != "plain":
                    continue
                text_value = self._sanitize_captured_plain_text(item.get("text"))
                if text_value:
                    captured_text_parts.append(text_value)
        return "\n".join(captured_text_parts).strip()

    def _filter_incompatible_proactive_framework_tools(
        self,
        req: ProviderRequest,
        names: set[str] | None = None,
    ) -> list[str]:
        tool_set = getattr(req, "func_tool", None)
        remove_tool = getattr(tool_set, "remove_tool", None)
        if not callable(remove_tool):
            return []
        excluded = {str(name).strip() for name in (names or {"AIsearch"}) if str(name).strip()}
        existing = {
            str(getattr(tool, "name", "") or "").strip()
            for tool in list(getattr(tool_set, "tools", []) or [])
        }
        removed = sorted(name for name in excluded if name in existing)
        for name in removed:
            remove_tool(name)
        if removed:
            logger.info(
                "主动主链已隔离不兼容全局工具: %s",
                ",".join(removed),
            )
        return removed
