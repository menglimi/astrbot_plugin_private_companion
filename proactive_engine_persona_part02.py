# -*- coding: utf-8 -*-
"""ProactiveEnginePersonaPart02Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_persona.py 机械抽取（6 个方法 + 0 个模块级名字 + 0 个类级赋值 / 469 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEnginePersonaMixin）。
"""
from __future__ import annotations

from .proactive_engine_persona_shared import logger
from .proactive_engine_persona_shared import Any
from .proactive_engine_persona_shared import PROACTIVE_ROUTE_REGISTRY
from .proactive_engine_persona_shared import PromptRenderMode
from .proactive_engine_persona_shared import PromptSection
from .proactive_engine_persona_shared import _engine_host
from .proactive_engine_persona_shared import _persona_provider_id
from .proactive_engine_persona_shared import _safe_float
from .proactive_engine_persona_shared import _safe_int
from .proactive_engine_persona_shared import _single_line
from .proactive_engine_persona_shared import core_memory_usage_contract_section
from .proactive_engine_persona_shared import datetime
from .proactive_engine_persona_shared import prompt_section
from .proactive_engine_persona_shared import render_prompt_sections
from .proactive_engine_persona_shared import runtime_persona_setting
from .proactive_engine_persona_shared import time



class ProactiveEnginePersonaPart02Mixin:
    """ProactiveEnginePersonaPart02Mixin（从 ProactiveEnginePersonaMixin 拆出）。"""


    def _apply_proactive_model_judgement_policy(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
    ) -> dict[str, Any]:
        result = dict(judgement)
        decision = _single_line(result.get("decision"), 20).lower()
        if decision not in {"defer", "drop"}:
            return result
        if self._proactive_model_judgement_requires_hard_block(user, result):
            result["hard"] = True
            return result
        original_reason = _single_line(result.get("reason"), 120) or "质量建议"
        has_rewrite = any(
            _single_line(result.get(key), 180)
            for key in ("reason_field", "action", "topic", "motive")
        )
        result["advisory_decision"] = decision
        result["decision"] = "rewrite" if has_rewrite else "send"
        result["delay_minutes"] = 0
        result["reason"] = self._normalize_legacy_proactive_text(
            f"软质量建议已交给正文生成：{original_reason}",
            limit=140,
        )
        return result

    def _proactive_source_model_hint_section(
        self,
        user: dict[str, Any],
    ) -> PromptSection | None:
        source = self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40)
        reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)

        def authored_hint(key: str, title: str, lines: tuple[str, ...]) -> PromptSection:
            return prompt_section(
                key=key,
                title=title,
                source="proactive_engine",
                content="\n".join(lines),
            )

        if source in {"pending_followup", "followup"}:
            return authored_hint(
                "proactive.source.followup",
                "来源专项改写：补一句",
                (
                    "这类来源不是重新开话题，而是前一句还有一个具体点没落地。",
                    "如果现在的 topic/motive 只是“再说一句/补一句/轻轻放一句/顺着那股劲”，优先 rewrite，不要直接 send。",
                    "rewrite 后必须补出一个实质点：提醒、遗漏信息、没说完的小重点，或前一句里还挂着的小事。",
                    "情绪可以很轻，但只允许当底色：一点点不甘心、惦记，或认真；不要把情绪本身写成内容。",
                ),
            )
        if source == "daily_greeting" or reason in {"morning_greeting", "noon_greeting", "evening_greeting"}:
            return authored_hint(
                "proactive.source.greeting",
                "来源专项改写：日常招呼",
                (
                    "这类来源的价值在“当天这个时段自然出现的一次招呼”，不是机械签到，也不是在任何空档里补一句模板问候。",
                    "不要因为今天先发过其他话题就默认 morning_greeting 已经完成；应判断此前正文里是否真的自然说过早安或明确打过晨间招呼。已经说过就不重复，尚未说过且仍在合适窗口内则可以顺着当前生活片段自然开口。",
                    "用户先自然来聊不等于 Bot 已经醒来，也不必因此取消首次起床问候；但若双方已经在早晨连续聊了一阵，就避免突兀地补正式早安。",
                    "noon_greeting/evening_greeting 仍要避开刚刚发生的来回互动。",
                    "rewrite 后必须落在当前时段的一个小片段上：早晨刚醒/洗漱/出门前，中午刚吃完/发懒/准备午休，晚上收尾/回家/窝下来。",
                    "最终效果要像这个时段第一次顺手冒头，不像模板化签到，也不像聊到一半又补来的礼貌问候。",
                ),
            )
        if source == "random":
            return authored_hint(
                "proactive.source.random",
                "来源专项改写：轻微想念",
                (
                    "规则层已经判断这次“想来找一下”成立，但正文不能只剩关系姿态。",
                    "如果现在的 topic/motive 只有“想你了/来看看你/在不在/忙不忙”，优先 rewrite。",
                    "rewrite 后要补出一个很小的具体钩子：当前时段的小片段、刚冒出来的小想法，或一句能自然开口的话。",
                    "这类来源只能轻，不要写成索取回应，也不要写成无缘由的空泛表白。",
                ),
            )
        if source == "state" or reason == "state_share":
            return authored_hint(
                "proactive.source.state",
                "来源专项改写：身体小需求",
                (
                    "这类来源不是汇报状态，而是身体上的那点小事带出来的话头。",
                    "如果现在的 topic/motive 像“我饿了/我累了/状态不好”，优先 rewrite。",
                    "rewrite 后要把它改成一个具体可聊的小需求，比如吃什么、要不要垫一口、想不想来点甜的；不要写成状态播报。",
                    "语气要自然，不要像健康汇报、撒娇表演或硬找人陪。",
                ),
            )
        if source == "mobile_location" or _single_line(user.get("planned_mobile_location_event_type"), 32):
            event_type = _single_line(user.get("planned_mobile_location_event_type"), 32)
            if event_type == "home_arrival":
                return authored_hint(
                    "proactive.source.home_arrival",
                    "来源专项改写：回家后的自然开口",
                    (
                        "用户刚进入已标记的家，允许自然提到“刚到家/回来了/先歇一会儿”这类生活片段。",
                        "不要提定位、坐标、手机、设备、监听或内部判断，也不要写成系统通知；像你自己顺手想到后说一句。",
                        "优先一句短而具体的话，避免连续追问“到家了吗/在家吗”。",
                    ),
                )
            return authored_hint(
                "proactive.source.location",
                "来源专项改写：位置转场",
                (
                    "只能把已确认的地点变化当作轻量生活背景，不暴露定位或设备细节。",
                ),
            )
        return None

    def _format_proactive_source_model_hint(self, user: dict[str, Any]) -> str:
        section = self._proactive_source_model_hint_section(user)
        return (
            render_prompt_sections([section], mode=PromptRenderMode.LABELED_BLOCK)
            if section is not None
            else ""
        )

    @staticmethod
    def _format_proactive_user_message_freshness(
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> str:
        check_now = _engine_host._now_ts() if now is None else now
        current_time = datetime.fromtimestamp(check_now).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        last_user_at = _safe_float(user.get("last_user_message_at"), 0)
        if last_user_at <= 0:
            return "\n".join(
                (
                    f"- 当前时间：{current_time}",
                    "- 最近用户消息时间：未知；只能把消息原文当历史记录，不能推断为刚刚发生。",
                )
            )
        age_seconds = max(0.0, check_now - last_user_at)
        if age_seconds < 60:
            age_text = f"{int(age_seconds)} 秒前"
        elif age_seconds < 3600:
            age_text = f"{age_seconds / 60:.1f} 分钟前"
        elif age_seconds < 86400:
            age_text = f"{age_seconds / 3600:.2f} 小时前"
        else:
            age_text = f"{age_seconds / 86400:.2f} 天前"
        last_user_time = datetime.fromtimestamp(last_user_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        return "\n".join(
            (
                f"- 当前时间：{current_time}",
                f"- 最近用户消息时间：{last_user_time}（{age_text}）",
                "- 最近用户消息是带时间的历史原文，不等于用户当前仍处于当时状态。跨越明显时段后，旧的晚安、吃饭、出门、忙碌等内容不能改写成用户刚刚说过或正在发生。",
            )
        )

    def _format_proactive_model_judge_prompt(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> str:
        persona = str(self._get_default_persona_prompt() or "").strip()
        worldview_sections_getter = getattr(self, "_format_worldview_adaptation_prompt_sections", None)
        worldview_sections = (
            list(worldview_sections_getter() or ())
            if callable(worldview_sections_getter)
            else []
        )
        worldview = (
            str(self._format_worldview_adaptation_prompt() or "").strip()
            if not worldview_sections
            else ""
        )
        boundary_section_getter = getattr(self, "_format_private_user_boundary_prompt_section", None)
        boundary_section = (
            boundary_section_getter(user)
            if isinstance(user, dict) and callable(boundary_section_getter)
            else None
        )
        if boundary_section is None and isinstance(user, dict):
            boundary_fallback = getattr(self, "_format_private_user_boundary_hint", None)
            if callable(boundary_fallback):
                fallback_text = str(boundary_fallback(user) or "").strip()
                if fallback_text:
                    boundary_section = prompt_section(
                        key="proactive.judge.relationship_boundary",
                        title="关系边界",
                        source="proactive_engine",
                        content=fallback_text,
                    )
        rel_summary = ""
        formatter = getattr(self, "_format_relationship_summary", None)
        if callable(formatter):
            try:
                rel_summary = _single_line(formatter(user), 220)
            except Exception:
                rel_summary = ""
        local_alignment = self._planned_proactive_persona_alignment(user)
        semantics = self._planned_proactive_semantics(user)
        window_phase, window_detail = self._planned_impulse_window_phase(user)
        inner_readiness = self._proactive_inner_readiness(user)
        source_hint_section = self._proactive_source_model_hint_section(user)
        voice_section_getter = getattr(self, "_format_persona_voice_channel_prompt_section", None)
        planning_voice_section = voice_section_getter("planning") if callable(voice_section_getter) else None
        inner_voice_section = voice_section_getter("inner") if callable(voice_section_getter) else None
        role = self._private_user_role(user)
        nickname = _single_line(user.get("nickname"), 40) or runtime_persona_setting(
            self, "default_nickname", "你"
        )
        message_freshness = self._format_proactive_user_message_freshness(user, now=now)
        calendar_constraint_section = None
        calendar_hint_getter = getattr(self, "_format_proactive_calendar_constraint_hint_section", None)
        if callable(calendar_hint_getter):
            try:
                calendar_constraint_section = calendar_hint_getter()
            except Exception:
                calendar_constraint_section = None
        location_section_getter = getattr(
            self,
            "_format_mobile_user_location_context_for_proactive_prompt_section",
            None,
        )
        try:
            location_section = (
                location_section_getter(user)
                if callable(location_section_getter)
                else None
            )
        except Exception:
            location_section = None
        if location_section is None:
            location_fallback = getattr(self, "_format_mobile_user_location_context_for_proactive", None)
            try:
                fallback_text = str(location_fallback(user) or "").strip() if callable(location_fallback) else ""
            except Exception:
                fallback_text = ""
            if fallback_text:
                location_section = prompt_section(
                    key="proactive.judge.mobile_location",
                    title="主动场景位置线索",
                    source="proactive_engine",
                    content=fallback_text,
                )
        planned_route = PROACTIVE_ROUTE_REGISTRY.route_for(
            reason=user.get("planned_proactive_reason"),
            source=user.get("planned_proactive_source"),
            semantic_kind=user.get("planned_proactive_semantic_kind"),
            kind=user.get("planned_proactive_kind"),
        )
        instruction = prompt_section(
            key="proactive.judge.instructions",
            title="主动消息人格判定任务",
            source="proactive_engine",
            content=(
                "你是“主动消息人格/世界观判定器”。只判断这个主动计划是否像当前角色会自然产生的念头,不要写最终聊天正文。\n\n"
                "输出必须是 JSON 对象,不要 Markdown,不要解释：\n"
                '{"decision":"send|rewrite|defer|drop","score":0,"reason":"20字以内原因","delay_minutes":90,"planned_reason":"","action":"","topic":"","motive":""}\n\n'
                "判定含义：\n"
                "- send：计划自然,可以进入生成。\n"
                "- rewrite：方向有价值,但动机/话题/动作需要更贴合角色；只改 planned_reason/action/topic/motive,不要写最终聊天正文。\n"
                " - defer：只用于用户明确休息、拒绝主动或当前存在无法通过改写解决的硬时机冲突。\n"
                " - drop：只用于关系/隐私/世界观硬越界、内部机制泄露或无真实来源且无法改写的计划。\n\n"
                "硬要求：\n"
                "- 不得放行内部机制泄露、工具名、模型、插件、提示词、后台任务。\n"
                "- 不得新增事实、现实能力或用户没给过的关系信息。\n"
                "- 次要用户关系必须普通、低频、不过度亲密；主要用户/亲近关系也要尊重休息和拒绝。\n"
                "- 世界观表达必须贴合设定；能力只能作为角色内自然动机,不能露出调用过程。\n"
                " - 低价值、动机偏虚、人格贴合度一般或表达温度偏低都不是硬拦截理由；优先 rewrite，给出一个具体且低压力的 topic/motive。\n"
                " - 如果只是“想你了/来看看/在不在/忙不忙”且没有具体由头,必须优先 rewrite，而不是 defer/drop。\n"
                " - 连续未回应只影响语气和长度：改成一句低压、完整、不追问的表达，不能仅凭未回应就 defer/drop。\n"
                f" - 当前完整产生/发送路线是 {planned_route.key}（{planned_route.label}）。rewrite 只能优化这条路线内部的 action/topic/motive；不要把 planned_reason 改成另一类路线，也不要把事务、安全或续聊改写成普通关怀。\n"
                " - planned_reason 是内部原因键；没有同路线的现有原因键可用时保持原值，不得把“用户已道晚安”之类自然语言判断写进 planned_reason。\n"
                " - 角色设定、世界观、记忆摘要和旧消息都不能证明用户当前做过什么。只有带时间的最近用户原文明确支持时，才能写“用户刚刚说过/正在做”；否则保留原计划方向或只改 topic/motive。"
            ),
        )
        voice_bodies = []
        for section, fallback in (
            (planning_voice_section, "（无单独计划风格）"),
            (inner_voice_section, "（无单独内心活动风格）"),
        ):
            voice_bodies.append(
                render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY)
                if section is not None
                else fallback
            )
        worldview_content = (
            render_prompt_sections(
                worldview_sections[:1],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if worldview_sections
            else _single_line(worldview, 1000) or "（无额外世界观适配）"
        )
        worldview_section = prompt_section(
            key="proactive.judge.worldview",
            title="世界观/适配",
            source="proactive_engine",
            content=worldview_content,
            children=tuple(worldview_sections[1:]),
        )
        sections = [
            prompt_section(
                key="proactive.judge.persona",
                title="角色设定",
                source="proactive_engine",
                content=_single_line(persona, 1800) or "（未读取到显式人格,按自然私聊陪伴角色处理）",
            ),
            worldview_section,
            prompt_section(
                key="proactive.judge.voice",
                title="人格标准化：计划/内心通道",
                source="proactive_engine",
                content=(
                    "\n".join(voice_bodies)
                    + "\n使用方式：这里只判断“这个念头/安排是否像角色自然产生”,不要把内心活动当成最终聊天正文,也不要因为风格规则而新增事实。"
                ),
            ),
        ]
        if boundary_section is not None:
            sections.append(boundary_section)
        sections.extend(
            (
                prompt_section(
                    key="proactive.judge.target",
                    title="当前对象",
                    source="proactive_engine",
                    content=(
                        f"- 昵称：{nickname}\n"
                        f"- 关系角色：{role}\n"
                        f"- 关系摘要：{rel_summary or '暂无'}\n"
                        f"- 连续未回应：{_safe_int(user.get('ignored_streak'), 0, 0)}\n"
                        f"- 最近用户消息：{_single_line(user.get('last_user_message'), 160) or '（无）'}\n"
                        f"- 最近 Bot 消息：{_single_line(user.get('last_companion_message'), 160) or '（无）'}\n"
                        f"- Bot 当前开口欲/主动表达温度：{_safe_float(inner_readiness.get('score'), 0.55):.2f}｜{_single_line(inner_readiness.get('label'), 60)}｜{_single_line(inner_readiness.get('detail'), 180)}"
                    ),
                ),
                prompt_section(
                    key="proactive.judge.freshness",
                    title="消息时效",
                    source="proactive_engine",
                    content=message_freshness,
                ),
            )
        )
        for optional_section in (calendar_constraint_section, location_section):
            if optional_section is not None:
                sections.append(optional_section)
        sections.append(
            prompt_section(
                key="proactive.judge.plan",
                title="当前主动计划",
                source="proactive_engine",
                content=(
                    f"- route：{planned_route.key}（{planned_route.label}）\n"
                    f"- source：{self._normalize_legacy_proactive_text(user.get('planned_proactive_source'), limit=40) or 'unknown'}\n"
                    f"- reason：{self._normalize_legacy_proactive_text(user.get('planned_proactive_reason'), limit=40) or 'check_in'}\n"
                    f"- action：{_single_line(user.get('planned_proactive_action'), 40) or 'message'}\n"
                    f"- topic：{_single_line(user.get('planned_proactive_topic'), 100) or '无'}\n"
                    f"- motive：{_single_line(user.get('planned_proactive_motive'), 220) or '无'}\n"
                    f"- 候选语义：{_single_line(semantics.get('kind'), 40)}/{_single_line(semantics.get('anchor_type'), 40)}｜score={_safe_float(semantics.get('score'), 0.5):.2f}｜pressure={_safe_float(semantics.get('pressure'), 0.4):.2f}｜risk={_safe_float(semantics.get('risk'), 0.0):.2f}｜{_single_line(semantics.get('note'), 140)}\n"
                    f"- 念头窗口：{window_phase}｜{window_detail}\n"
                    f"- 本地粗判：{_safe_float(local_alignment.get('score'), 0.0):.2f}｜{_single_line(local_alignment.get('note'), 140)}"
                ),
            )
        )
        if source_hint_section is not None:
            sections.append(source_hint_section)
        return "\n\n".join(
            (
                render_prompt_sections([instruction], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(sections, mode=PromptRenderMode.LABELED_BLOCK),
            )
        )

    async def _review_planned_proactive_with_model(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        check_now = _engine_host._now_ts() if now is None else now
        if not bool(runtime_persona_setting(self, "enable_llm_proactive_persona_judge", True)):
            return {"decision": "send", "score": 100, "reason": "模型人格判定关闭"}
        if self._normalize_legacy_proactive_text(user.get("planned_proactive_source"), limit=40) in {"timer", "troubleshooting", "simulation"}:
            return {"decision": "send", "score": 100, "reason": "特权计划跳过模型人格判定"}
        signature = self._planned_proactive_model_judge_signature(user)
        cached = self._cached_proactive_model_judgement(user, signature=signature, now=check_now)
        if isinstance(cached, dict):
            cached = self._apply_proactive_model_judgement_policy(user, cached)
            cached["cached"] = True
            return cached
        local_result = self._local_proactive_persona_judgement(user)
        if isinstance(local_result, dict):
            return local_result
        daily_limit = _safe_int(
            runtime_persona_setting(self, "proactive_persona_judge_max_daily", 12),
            12,
            0,
            100,
        )
        if daily_limit <= 0 or self._proactive_persona_judge_calls_today() >= daily_limit:
            return {"decision": "send", "score": 0, "reason": "模型日预算已满，使用本地规则", "local": True}
        prompt = self._format_proactive_model_judge_prompt(user, now=check_now)
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息适合性",
                    _single_line(user.get("planned_proactive_reason"), 80),
                    _single_line(user.get("planned_proactive_topic"), 120),
                    _single_line(user.get("planned_proactive_motive"), 180),
                    "用户习惯 上次主动回应 边界 当前穿搭 当前日程 最近状态",
                )
                if part
            )
            memory_context = await memory_getter(
                kind="proactive_review",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=800,
            )
            if memory_context:
                memory_sections = [
                    prompt_section(
                        key="proactive.judge.memory",
                        title="我会牢牢记住你 相关记忆",
                        source="proactive_engine",
                        content=(
                            "<!-- private_companion_memory_review_context_v1 -->\n"
                            f"{memory_context}\n"
                            "使用方式：只辅助判断是否适合主动、是否需要改写或延后；不要在理由里暴露检索过程。"
                        ),
                    )
                ]
                core_memory_section = core_memory_usage_contract_section(
                    memory_context,
                    stage="review",
                )
                if core_memory_section is not None:
                    memory_sections.append(core_memory_section)
                prompt = f"{prompt.rstrip()}\n\n{render_prompt_sections(memory_sections, mode=PromptRenderMode.LABELED_BLOCK)}"
        started = time.perf_counter()
        raw = await self._llm_call(
            prompt,
            max_tokens=260,
            provider_id=self._task_provider(
                _persona_provider_id(
                    self,
                    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
                    "proactive_persona_judge_provider_id",
                    "complex",
                ),
                _persona_provider_id(
                    self, "RESPONSE_REVIEW_PROVIDER_ID", "response_review_provider_id", "fast"
                ),
                _persona_provider_id(self, "MAI_STYLE_PROVIDER_ID", "mai_style_provider_id", "fast"),
            ),
            task="proactive_persona_judge",
        )
        parsed = self._parse_json_object(raw)
        result = self._normalize_proactive_model_judgement(parsed)
        if not isinstance(result, dict):
            logger.info("模型人格判定无有效 JSON,降级本地判定")
            return {"decision": "send", "score": 0, "reason": "模型判定失败,降级本地"}
        result["signature"] = signature
        result = self._apply_proactive_model_judgement_policy(user, result)
        result["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        logger.info(
            "主动模型人格判定: decision=%s score=%s reason=%s elapsed=%sms",
            result.get("decision"),
            result.get("score"),
            _single_line(result.get("reason"), 100),
            result.get("elapsed_ms"),
        )
        return result
