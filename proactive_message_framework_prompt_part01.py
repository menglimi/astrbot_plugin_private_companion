# -*- coding: utf-8 -*-
"""ProactiveMessageFrameworkPromptPart01Mixin。

由 tools/split_mixin_domain.py 从 proactive_message_framework_prompt.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 747 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveMessageFrameworkPromptMixin）。
"""
from __future__ import annotations

from .proactive_message_framework_prompt_shared import _now_ts
from .proactive_message_framework_prompt_shared import Any
from .proactive_message_framework_prompt_shared import PromptDocumentPart
from .proactive_message_framework_prompt_shared import PromptRenderMode
from .proactive_message_framework_prompt_shared import PromptSection
from .proactive_message_framework_prompt_shared import _ACTION_TEXT
from .proactive_message_framework_prompt_shared import _PROACTIVE_DOCUMENT_RENDER
from .proactive_message_framework_prompt_shared import _REASON_TEXT
from .proactive_message_framework_prompt_shared import _external_schedule_material_context
from .proactive_message_framework_prompt_shared import _proactive_prompt_part
from .proactive_message_framework_prompt_shared import _safe_float
from .proactive_message_framework_prompt_shared import _safe_int
from .proactive_message_framework_prompt_shared import _single_line
from .proactive_message_framework_prompt_shared import core_memory_usage_contract_section
from .proactive_message_framework_prompt_shared import datetime
from .proactive_message_framework_prompt_shared import prompt_document
from .proactive_message_framework_prompt_shared import prompt_section
from .proactive_message_framework_prompt_shared import render_prompt_document
from .proactive_message_framework_prompt_shared import render_prompt_sections
from .proactive_message_framework_prompt_shared import runtime_persona_setting



class ProactiveMessageFrameworkPromptPart01Mixin:
    """ProactiveMessageFrameworkPromptPart01Mixin（从 ProactiveMessageFrameworkPromptMixin 拆出）。"""


    async def _build_framework_proactive_prompt(
        self,
        *,
        user: dict[str, Any],
        name: str,
        reason: str,
        action: str,
        action_context: str,
        motive: str,
    ) -> str:
        relationship_sanitizer = getattr(self, "_sanitize_generation_relationship_context", None)

        def sanitize_relationship_source(value: Any, source: str) -> str:
            if callable(relationship_sanitizer):
                try:
                    return relationship_sanitizer(value, source=source)
                except Exception:
                    pass
            return str(value or "").strip()

        state = self.data.get("daily_state", {})
        action_prompt_context = sanitize_relationship_source(
            self._format_action_prompt_context(action, action_context),
            "proactive.action_context",
        )
        relationship_fact = self._format_proactive_relationship_fact(user)
        current_item = self._proactive_current_plan_item(self.data.get("daily_plan", {}))
        current_schedule = self._format_schedule_context_for_prompt() or self._format_plan_item_for_prompt(current_item)
        troubleshooting_section = self._proactive_troubleshooting_request_prompt_section(user)
        source_focused_reasons = {
            "bili_video_share",
            "news_share",
            "web_exploration_share",
            "creative_share",
        }
        if troubleshooting_section is not None:
            current_schedule = "（本轮不使用生活片段；只按用户刚发起的测试请求自然开口，不补写虚构见闻）"
        elif reason in source_focused_reasons:
            current_schedule = "（本轮不取生活片段，只围绕主动来源本身）"
        elif reason == "goodnight_screen_check":
            current_schedule = "（本轮不取生活片段、旧记忆或屏幕内容，只轻声提醒一次早点休息）"
        elif reason in {"meal_care", "meal_care_followup"}:
            current_schedule = (
                "（饭点关心只使用当前时间、饭点和本轮动机；"
                "不引用模拟日程中的具体动作、见闻、message_seed 或旧饮食记录）"
            )
        elif reason == "group_share":
            last_sidecar_at = _safe_float(user.get("last_group_share_life_sidecar_at"), 0)
            if last_sidecar_at > 0 and _now_ts() - last_sidecar_at < 6 * 3600:
                current_schedule = "（最近群分享已经顺手带过生活片段，本轮只围绕群里那件事）"
        external_material = ""
        if troubleshooting_section is None and reason not in source_focused_reasons and reason not in {
            "goodnight_screen_check",
            "meal_care",
            "meal_care_followup",
        }:
            external_material = await _external_schedule_material_context(
                self,
                kind="proactive",
                max_chars=900,
            )
        state_hint = self._format_state_for_framework_prompt(
            state if isinstance(state, dict) else {},
            reason=reason,
            action=action,
        )
        state_hint = self._sanitize_owner_environment_context_for_private_user(state_hint, user)
        state_hint = sanitize_relationship_source(state_hint, "proactive.current_state")
        location_section_formatter = getattr(
            self,
            "_format_mobile_user_location_context_for_proactive_prompt_section",
            None,
        )
        try:
            location_section = (
                location_section_formatter(user)
                if callable(location_section_formatter)
                else None
            )
        except Exception:
            location_section = None
        anonymous_area_section: PromptSection | None = None
        if reason in {"anonymous_area_dwell", "anonymous_area_familiarity"}:
            anonymous_area_section = prompt_section(
                key=(
                    "proactive.anonymous_area_departure"
                    if reason == "anonymous_area_dwell"
                    else "proactive.anonymous_area_familiarity"
                ),
                title=(
                    "离开后的模糊熟悉感"
                    if reason == "anonymous_area_dwell"
                    else "重复到访后的模糊熟悉感"
                ),
                source="proactive_message",
                content=(
                    "这是一条位置相关但延迟表达的生活念头：用户已经离开一个没有命名的区域。"
                    "不要提城市、城区、地图、高德、定位、停留时长或‘我知道你在哪里’，也不要追问具体地点。"
                    "只把它写成后来想起的一点生活关心；如果觉得不自然，可以只分享一句轻松的近况，不必提问。"
                    if reason == "anonymous_area_dwell"
                    else (
                        "这是一条从多次匿名区域到访形成的轻微熟悉感。不要声称知道用户有固定去处，"
                        "不要提城市、城区、地图、高德、定位、次数或地点名称；用‘最近好像有个常去的地方’这类开放表达，"
                        "把是否解释留给用户，也可以完全不点破这份观察。"
                    )
                ),
            )
        mobile_arrival_section: PromptSection | None = None
        if _single_line(user.get("planned_mobile_location_event_type"), 32) == "home_arrival":
            mobile_arrival_section = prompt_section(
                key="proactive.home_arrival",
                title="回家后的自然开口",
                source="proactive_message",
                content=(
                    "用户刚进入已标记的家，可以自然提到刚到家、回来了或先歇一会儿。"
                    "不要提定位、坐标、手机、设备或监听，也不要写成系统通知；像顺手想到后说一句。"
                ),
            )
        timer_hint = self._format_llm_timer_context(user)
        time_guard = self._proactive_time_guard_hint(reason, current_item)
        deferred_share_tense_section = self._deferred_immediate_share_tense_prompt_section(user, action)
        future_schedule_section = self._format_proactive_future_schedule_hint_section(reason=reason)
        calendar_constraint_section = self._format_proactive_calendar_constraint_hint_section()
        recent_topics_hint = self._format_recent_proactive_topics_hint(user)
        # Search for unresolved open-loop / promise memories from the memory plugin
        open_loops_section: PromptSection | None = None
        try:
            umo = str(user.get("umo") or "").strip()
            if umo:
                open_loops = await self._memory_companion_search_open_loops(session_id=umo, limit=2)
                if open_loops:
                    loop_texts = []
                    for loop in open_loops[:2]:
                        content_preview = _single_line(
                            sanitize_relationship_source(
                                loop.get("content"),
                                "proactive.open_loop",
                            ),
                            80,
                        )
                        if not content_preview:
                            continue
                        age = loop.get("age_days")
                        created_ts = _safe_float(loop.get("created_ts"), 0.0)
                        created_at = _single_line(loop.get("created_at"), 40)
                        if created_ts <= 0 and created_at:
                            try:
                                created_ts = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
                            except (TypeError, ValueError, OverflowError):
                                created_ts = 0.0
                        if created_ts > 0:
                            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
                            if age_hours < 1:
                                age_text = "不到1小时"
                            elif age_hours < 24:
                                age_text = f"约{max(1, int(age_hours))}小时"
                            else:
                                age_text = f"约{max(1, int(age_hours / 24))}天"
                            age_str = f"（记录于 {datetime.fromtimestamp(created_ts).strftime('%Y-%m-%d %H:%M')}，距今{age_text}）"
                        elif age is not None:
                            age_str = f"（约{age:.0f}天前）"
                        else:
                            age_str = ""
                        loop_texts.append(f"- {content_preview}{age_str}")
                    if loop_texts:
                        open_loops_section = prompt_section(
                            key="proactive.open_loops",
                            title="未完成话题候选",
                            source="proactive_message",
                            content=(
                                "这些只是可选候选，不是必须提起的任务。本轮主动动机、当前用户消息和最近私聊实况优先级更高；"
                                "只有候选与它们有明确语义贴合，或你本来就是想兑现这件事时，才轻轻带一句。"
                                "如果不贴，就先放着，不得把旧话题变成本轮开场、主线或回复第一句，也不要为了连续性改变当前动机。\n"
                                + "\n".join(loop_texts)
                            ),
                        )
        except Exception:
            pass
        current_schedule = self._sanitize_schedule_context_for_private_user(current_schedule, user)
        current_schedule = sanitize_relationship_source(current_schedule, "proactive.current_schedule")
        compact_motive = _single_line(
            sanitize_relationship_source(motive, "proactive.planned_motive"),
            36,
        ) or "有一点想靠近对方"
        topic_hint = _single_line(
            sanitize_relationship_source(
                user.get("planned_proactive_topic"),
                "proactive.planned_topic",
            ),
            40,
        )
        unanswered_count = _safe_int(user.get("ignored_streak"), 0)
        unanswered_hint = f"此前连续 {unanswered_count} 次主动还没等到回复。" if unanswered_count > 0 else ""
        awaiting_since = _safe_float(user.get("awaiting_reply_since"), 0)
        unanswered_afterglow_section: PromptSection | None = None
        if unanswered_count > 0 and awaiting_since > 0:
            unanswered_afterglow_section = prompt_section(
                key="proactive.unanswered_afterglow",
                title="上一条主动的余波",
                source="proactive_message",
                content=(
                    "上一条主动消息目前还没有收到回应。这只是背景事实，不要求你在正文里点破；"
                    "由你根据当前关系和动机决定是否轻轻带过。若提及，只能像熟人自然察觉到对方沉默，"
                    "不能质问、催促、索取解释或写成‘你怎么不回我’。"
                ),
            )
        burst_section: PromptSection | None = None
        if bool(user.get("planned_proactive_burst")):
            burst_section = prompt_section(
                key="proactive.burst",
                title="同一阵念头的短连发",
                source="proactive_message",
                content=(
                    "这是同一阵主动念头里的后一条独立消息，不是上一条的分段；换一个更短、更口语的角度，"
                    "不要复述上一条，也不要因此连续追问。"
                ),
            )
        expression_shape_section = self._proactive_expression_shape_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        current_time = self._environment_now().strftime("%Y-%m-%d %H:%M")
        persona = await self._resolve_proactive_persona_prompt(user)
        recent_history_hint = ""
        try:
            recent_history_hint = await self._recent_private_conversation_for_proactive_review(
                user,
                limit=self._proactive_history_limit("generation"),
            )
        except Exception:
            recent_history_hint = ""
        recent_history_hint = sanitize_relationship_source(
            recent_history_hint,
            "proactive.recent_private_history",
        )
        recent_topics_hint = sanitize_relationship_source(
            recent_topics_hint,
            "proactive.recent_topics",
        )
        temporal_grounding_section = prompt_section(
            key="proactive.temporal_grounding",
            title="时间锚定",
            source="proactive_message",
            content=(
                f"- 当前真实时间：{current_time}。\n"
                "- 优先贴今天最新私聊、当前日程和当前时段；旧记忆只能作背景，不要改写成今天/现在正在发生。\n"
                "- 如果记忆或历史里是昨天、昨晚、之前的天气/通勤/身体状态，除非当前日程或最新私聊明确延续，否则不要拿来当本轮主动切口。\n"
                "- 如果必须提旧事，要明确说“昨晚/昨天/那次”，不要写成“今天刚遇到/现在还在/刚才发生”。"
            ),
        )
        relationship_initiative_section = self._format_proactive_relationship_initiative_prompt_section(
            user,
            reason=reason,
            action=action,
        )
        custom_template = str(runtime_persona_setting(self, "proactive_prompt_template", "") or "")
        template_document = (
            prompt_document(
                user_render=_PROACTIVE_DOCUMENT_RENDER,
                user=(
                    _proactive_prompt_part(prompt_section(
                        key="proactive.template.custom",
                        title="用户自定义主动消息模板",
                        source="proactive_message.config",
                        content=custom_template,
                    ), mode=PromptRenderMode.BODY_ONLY),
                ),
                metadata={"kind": "proactive_generation_template"},
            )
            if custom_template
            else self._default_proactive_prompt_document()
        )
        template_text = render_prompt_document(template_document)["user"]
        included_keys = {section.key for section in (*template_document.system, *template_document.user)}
        appended_parts: list[PromptDocumentPart] = []

        def make_part(
            section: PromptSection | None,
            *,
            mode: PromptRenderMode | None = None,
            prefix: str = "",
            separator_before: str = "\n\n",
        ) -> PromptDocumentPart | None:
            if section is None:
                return None
            return _proactive_prompt_part(
                section,
                mode=mode,
                prefix=prefix,
                separator_before=separator_before,
            )

        def render_part(part: PromptDocumentPart | None) -> str:
            if part is None:
                return ""
            return render_prompt_document(
                prompt_document(
                    user=(part,),
                    user_render=_PROACTIVE_DOCUMENT_RENDER,
                )
            )["user"]

        def append_part(part: PromptDocumentPart | None) -> None:
            if part is None:
                return
            section = part.section
            if not section.key or section.key in included_keys:
                return
            if not render_part(part):
                return
            appended_parts.append(part)
            included_keys.add(section.key)

        def append_section(
            section: PromptSection | None,
            *,
            mode: PromptRenderMode | None = None,
            prefix: str = "",
            separator_before: str = "\n\n",
        ) -> None:
            append_part(
                make_part(
                    section,
                    mode=mode,
                    prefix=prefix,
                    separator_before=separator_before,
                )
            )

        def sanitized_section(
            section: PromptSection | None,
            *,
            relationship_source: str,
        ) -> PromptSection | None:
            if section is None:
                return None
            body = sanitize_relationship_source(
                render_prompt_sections([section], mode=PromptRenderMode.BODY_ONLY),
                relationship_source,
            )
            if not body:
                return None
            return prompt_section(
                key=section.key,
                title=section.title,
                source=section.source,
                content=body,
                metadata=section.metadata,
            )

        worldview_adaptation = ""
        reason_text = _REASON_TEXT.get(reason, reason).replace("{name}", name)
        action_text = _ACTION_TEXT.get(action.split("+")[0], action).replace("{name}", name)
        replacements = {
            "{{name}}": name,
            "{{reason}}": reason_text,
            "{{action}}": action_text,
            "{{topic}}": topic_hint or "顺手递过来的一点东西",
            "{{motive}}": compact_motive,
            "{{style_hint}}": relationship_fact,
            "{{relationship_fact}}": relationship_fact,
            "{{state_hint}}": state_hint or "今天整体比较平稳。",
            "{{current_schedule}}": current_schedule if current_schedule and current_schedule != "（暂无）" else "（当前没有明确日程片段）",
            "{{time_guard}}": time_guard,
            "{{recent_topics}}": recent_topics_hint or "（无）",
            "{{content_options}}": "",
            "{{content_anchor}}": "",
            "{{ability_search}}": "",
            "{{action_boundary}}": "",
            "{{presence_layer}}": "",
            "{{worldview_adaptation}}": worldview_adaptation,
            "{{timer_hint}}": timer_hint or "",
            "{{action_context}}": action_prompt_context if action_prompt_context and action_prompt_context != "（无额外上下文）" else "什么都没做,就是忽然想来找你",
            "{{unanswered_count}}": str(unanswered_count) if unanswered_count > 0 else "",
            "{{unanswered_hint}}": unanswered_hint,
            "{{unanswered_afterglow_hint}}": render_part(make_part(unanswered_afterglow_section)),
            "{{burst_hint}}": render_part(make_part(burst_section)),
            "{{expression_shape_hint}}": render_part(make_part(expression_shape_section)),
            "{{open_loops_hint}}": render_part(make_part(open_loops_section)),
            "{{future_schedule_hint}}": render_part(make_part(future_schedule_section)),
            "{{current_time}}": current_time,
        }
        placeholder_sections = {
            "{{timer_hint}}": make_part(
                prompt_section(
                    key="proactive.timer_context",
                    title="主动定时上下文",
                    source="proactive_message.compat",
                    content=timer_hint,
                )
                if str(timer_hint or "").strip()
                else None,
                mode=PromptRenderMode.BODY_ONLY,
            ),
            "{{unanswered_afterglow_hint}}": make_part(unanswered_afterglow_section),
            "{{burst_hint}}": make_part(burst_section),
            "{{expression_shape_hint}}": make_part(expression_shape_section),
            "{{open_loops_hint}}": make_part(open_loops_section),
            "{{future_schedule_hint}}": make_part(future_schedule_section),
        }
        for token, part in placeholder_sections.items():
            if part is not None and token in template_text:
                included_keys.add(part.section.key)
                replacements[token] = render_part(part)
        prompt = template_text
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        for section in (
            unanswered_afterglow_section,
            burst_section,
            expression_shape_section,
            location_section if isinstance(location_section, PromptSection) else None,
            anonymous_area_section,
            mobile_arrival_section,
            future_schedule_section,
            calendar_constraint_section,
        ):
            append_section(section)
        if external_material:
            append_section(
                prompt_section(
                    key="proactive.external_material",
                    title="外部插件提供的今日实况（仅作生活素材，不得视为既定事实）",
                    source="proactive_message",
                    content=(
                        "它只是 Bot 听到或看到的外部动态；贴合当前切口时自然带过即可，不要提及来源插件名，"
                        "不要写成 Bot 亲身经历，也不要把它当成必须提起的事实。\n"
                        f"{external_material}"
                    ),
                )
            )
        if reason == "creative_share":
            append_section(self._creative_share_excerpt_prompt_section())
        route_section_getter = getattr(self, "_proactive_route_prompt_section", None)
        if callable(route_section_getter):
            route_section = route_section_getter(
                user,
                reason=reason,
                source=user.get("planned_proactive_source"),
            )
            if isinstance(route_section, PromptSection):
                append_section(route_section)
        quota_policy_getter = getattr(self, "_proactive_quota_policy", None)
        kind_getter = getattr(self, "_planned_proactive_kind", None)
        quota_tier = _safe_int(quota_policy_getter(user).get("tier"), 0, 0, 5) if callable(quota_policy_getter) else 0
        proactive_kind = kind_getter(user) if callable(kind_getter) else "relational"
        relaxed_unanswered_route = quota_tier >= 4 and proactive_kind in {"self_life", "content_share"}
        if unanswered_count >= 2 and not relaxed_unanswered_route:
            unanswered_boundary = prompt_section(
                key="proactive.unanswered_boundary",
                title="连续未回应时的成文边界",
                source="proactive_message",
                content=(
                    "- 这次优先只表达一个完整意思，用一句自然短句或两个紧密相连的短分句说完。\n"
                    "- 不要把近况、提问和叮嘱叠在同一条里；更适合分享后自然收住，不要求对方回复。\n"
                    "- 如果原本想说的内容较多，应重新组织成完整短句，绝不能留下主谓宾未完成的半句话。"
                ),
            )
            append_section(unanswered_boundary)
        elif unanswered_count >= 2 and relaxed_unanswered_route:
            relaxed_boundary = prompt_section(
                key="proactive.relaxed_unanswered_boundary",
                title="高配额生活流的未回应边界",
                source="proactive_message",
                content=(
                    "- 对方没有逐条回应不等于拒绝继续接收生活片段或可靠内容分享，不要因此突然写得疏远或只剩客套话。\n"
                    "- 本条仍应自成一件具体的事，不追问上一条、不催促、不抱怨，也不要暗示对方欠你回复。"
                ),
            )
            append_section(relaxed_boundary)
        persona_marker = "<!-- private_companion_proactive_persona_v1 -->"
        if persona:
            append_section(
                prompt_section(
                    key="proactive.persona",
                    title="当前主动消息必须遵循的人格",
                    source="proactive_message",
                    content=(
                        f"{self._truncate_proactive_context(persona, 2600)}\n"
                        "这份人格约束最终说话者的身份、性格、关系站位、称呼和措辞。"
                        "日程、记忆、主动动机及工具结果只能提供本轮内容，不能覆盖或改写人格。"
                    ),
                ),
                prefix=persona_marker,
            )
        proactive_voice_marker = "<!-- private_companion_proactive_voice_v1 -->"
        proactive_voice_sections_getter = getattr(self, "_format_proactive_voice_prompt_sections", None)
        if callable(proactive_voice_sections_getter):
            try:
                proactive_voice_sections = list(proactive_voice_sections_getter() or ())
            except Exception:
                proactive_voice_sections = []
            for index, proactive_voice_section in enumerate(proactive_voice_sections):
                if not isinstance(proactive_voice_section, PromptSection):
                    continue
                append_section(
                    proactive_voice_section,
                    prefix=proactive_voice_marker if index == 0 else "",
                )
        else:
            proactive_voice_getter = getattr(self, "_format_proactive_voice_prompt", None)
            proactive_voice = proactive_voice_getter() if callable(proactive_voice_getter) else ""
            proactive_voice = str(proactive_voice or "").strip()
            if proactive_voice:
                append_section(
                    prompt_section(
                    key="proactive.voice.compat",
                    title="主动消息说话方式",
                    source="main.compat",
                    content=proactive_voice,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                    prefix=proactive_voice_marker,
                )
        expression_section_getter = getattr(self, "_format_expression_voice_prompt_section", None)
        expression_voice_section = (
            expression_section_getter(
                scope="proactive",
                target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                context_owner=user,
                stage_owner=user,
            )
            if callable(expression_section_getter)
            else None
        )
        expression_voice_marker = "<!-- private_companion_expression_voice_v1 -->"
        if isinstance(expression_voice_section, PromptSection):
            append_section(
                expression_voice_section,
                prefix=expression_voice_marker,
            )
        elif not callable(expression_section_getter):
            expression_formatter = getattr(self, "_format_expression_voice_for_prompt", None)
            expression_voice = (
                expression_formatter(
                    scope="proactive",
                    target_id=_single_line(user.get("user_id") or user.get("id"), 80),
                    context_owner=user,
                    stage_owner=user,
                )
                if callable(expression_formatter)
                else ""
            )
            expression_voice = str(expression_voice or "").strip()
            if expression_voice:
                append_section(
                    prompt_section(
                    key="proactive.expression_voice.compat",
                    title="主动消息表达方式",
                    source="user_memory.compat",
                    content=expression_voice,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                    prefix=expression_voice_marker,
                )
        append_section(self._proactive_natural_delivery_prompt_section())
        append_section(deferred_share_tense_section)
        tool_boundary_section = prompt_section(
            key="proactive.tool_boundary",
            title="主动生成工具边界",
            source="proactive_message",
            content=(
                "- 这一轮只面向当前私聊对象，不调用任何转述、私聊发送、群发、QQ空间，"
                "也不调用除 `pc_generate_photo` 以外的其他 Private Companion 工具。\n"
                "- 当本轮主动动机、模板或当前生活场景确实适合用真实图片一起表达时，"
                "允许调用一次 `pc_generate_photo`（`send=true`）；不需要图片时只生成一句自然正文。\n"
                "- 主动链中的 `pc_generate_photo` 成图会由插件统一发送；工具确认 `delivery_deferred=true` 后，"
                "只输出工具要求的内部静默标记，不要补写生成成功、等待发送或图片已发送等回执。\n"
                "- `caption` 不是工具回执栏；只在有贴合当前情境的自然正文时填写。若只能写“图生好了/给你看”，就留空只发图片。\n"
                "- 生图成功后，不要再说相机没反应、下次再拍或上游失败；生图失败时按工具返回的 "
                "`final_response_instruction` 收束，本轮不要重试。\n"
                "- 不要写“已发送/已转述/消息已发给某人/工具执行完成”等状态回执。\n"
                "- 如果本轮 Provider/API 返回英文报错、内容策略拒绝、敏感词提示或政策链接，那是内部失败，不是给用户的正文；"
                "不要复述、翻译或润色，直接停止输出，交给插件稍后重试。\n"
                "- 如果想分享一件事，就直接把那句自然聊天内容写出来。"
            ),
        )
        append_section(tool_boundary_section)
        append_section(self._proactive_reaction_expression_prompt_section(action))
        append_section(self._proactive_visible_text_format_prompt_section(action))
        append_section(temporal_grounding_section)
        append_section(relationship_initiative_section)
        append_section(troubleshooting_section)
        if recent_history_hint:
            append_section(
                prompt_section(
                    key="proactive.recent_private_history",
                    title="最近私聊实况",
                    source="proactive_message",
                    content=(
                        f"{recent_history_hint}\n"
                        "使用方式：这是当前会话最近真实发生的内容。它优先级高于旧记忆；不要把更早的记录写成今天刚发生。"
                    ),
                )
            )
        if reason == "goodnight_screen_check":
            append_section(
                prompt_section(
                    key="proactive.goodnight_screen_boundary",
                    title="晚安识屏提醒边界",
                    source="proactive_message",
                    content=(
                        "- 内部状态只说明互道晚安后仍有明确活动迹象；没有向你提供屏幕画面、应用、窗口、账号或文字内容。\n"
                        "- 只生成一句轻声、低压力的休息提醒，可以说‘还没睡的话，忙完就早点休息’，但不要声称看见了屏幕或知道对方在做什么。\n"
                        "- 不提识屏、监控、查岗、电脑、软件、窗口、具体活动或任何隐私细节，不复述刚才的晚安。\n"
                        "- 不追问、不催促、不要求解释，也不要要求对方回复。"
                    ),
                )
            )
        body_health_section_getter = getattr(self, "format_health_prompt_section", None)
        if callable(body_health_section_getter):
            try:
                body_health_section = body_health_section_getter(user, reason=reason)
            except Exception:
                body_health_section = None
            if isinstance(body_health_section, PromptSection):
                append_section(
                    sanitized_section(
                        body_health_section,
                        relationship_source="proactive.body_health_hint",
                    )
                )
        balance_section_getter = getattr(self, "_format_balance_awareness_prompt_section", None)
        if callable(balance_section_getter):
            try:
                balance_section = balance_section_getter(user, reason=reason)
            except Exception:
                balance_section = None
            if isinstance(balance_section, PromptSection):
                append_section(
                    sanitized_section(
                        balance_section,
                        relationship_source="proactive.balance_hint",
                    )
                )
        typed_hint_specs = (
            (
                "_format_environment_change_prompt_section",
                "proactive.environment_hint",
            ),
            (
                "_format_weather_alert_prompt_section",
                "proactive.weather_alert_hint",
            ),
            (
                "_format_personal_goal_prompt_section",
                "proactive.personal_goal_hint",
            ),
            (
                "_format_memo_note_prompt_section",
                "proactive.memo_hint",
            ),
        )
        for getter_name, relationship_source in typed_hint_specs:
            hint_getter = getattr(self, getter_name, None)
            if not callable(hint_getter):
                continue
            try:
                hint_section = hint_getter(user, reason=reason)
            except Exception:
                hint_section = None
            if isinstance(hint_section, PromptSection):
                append_section(
                    sanitized_section(
                        hint_section,
                        relationship_source=relationship_source,
                    )
                )
        append_section(open_loops_section)
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            user_id = _single_line(user.get("user_id") or user.get("id"), 80)
            query = " ".join(
                part
                for part in (
                    "主动消息正文生成",
                    f"当前真实时间 {current_time}",
                    "当前日期 最新私聊 当前日程 当前时段 旧日材料不能改写成当前事实",
                    reason,
                    action,
                    topic_hint,
                    compact_motive,
                    "用户习惯 最近互动 当前穿搭 当前日程 自我时间线 避雷",
                )
                if _single_line(part, 180)
            )
            memory_context = await memory_getter(
                kind="proactive_generation",
                query=query,
                user=user,
                user_id=user_id,
                top_k=5,
                max_chars=760,
            )
        if memory_context:
            append_section(
                prompt_section(
                    key="proactive.memory_context",
                    title="我会牢牢记住你 可用记忆",
                    source="proactive_message",
                    content=(
                        f"{memory_context}\n"
                        "使用方式：只作为自然连续性和边界参考；能贴住当前切口就轻轻用,不相关就忽略。不要说“我查到/我记忆里”。"
                    ),
                ),
                prefix="<!-- private_companion_memory_generation_context_v1 -->",
            )
            append_section(core_memory_usage_contract_section(memory_context, stage="generation"))
        relationship_guard_getter = getattr(self, "_format_generation_relationship_authority_guard", None)
        if callable(relationship_guard_getter):
            try:
                relationship_guard = str(relationship_guard_getter() or "").strip()
            except Exception:
                relationship_guard = ""
            if relationship_guard:
                append_section(
                    prompt_section(
                        key="proactive.relationship_authority",
                        title="关系事实权限",
                        source="user_memory.compat",
                        content=relationship_guard,
                    ),
                    mode=PromptRenderMode.BODY_ONLY,
                )
        append_section(self._format_proactive_recipient_identity_guard_prompt_section(user, name))
        if self._proactive_llm_segmenting_allowed(umo=_single_line(user.get("umo"), 240)):
            segmenting_section_getter = getattr(self, "_llm_controlled_segmenting_prompt_section", None)
            if callable(segmenting_section_getter):
                try:
                    segmenting_section = segmenting_section_getter()
                except Exception:
                    segmenting_section = None
                if isinstance(segmenting_section, PromptSection):
                    append_section(
                        segmenting_section,
                        mode=PromptRenderMode.CONVERSATION_XML,
                    )
        suffix = render_prompt_document(
            prompt_document(
                user=tuple(appended_parts),
                user_render=_PROACTIVE_DOCUMENT_RENDER,
                metadata={"kind": "proactive_generation_appendix"},
            )
        )["user"]
        return "\n\n".join(part for part in (prompt.strip(), suffix) if part).strip()
