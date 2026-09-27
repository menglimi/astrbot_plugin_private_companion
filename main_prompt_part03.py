# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPromptPart03Mixin。

由 tools/split_mixin_domain.py 从 main_prompt.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 476 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPromptMixin）。
"""
from __future__ import annotations

from .main_prompt_shared import logger
from .main_prompt_shared import Any
from .main_prompt_shared import AstrMessageEvent
from .main_prompt_shared import PromptRenderMode
from .main_prompt_shared import PromptSection
from .main_prompt_shared import ProviderRequest
from .main_prompt_shared import _now_ts
from .main_prompt_shared import _safe_float
from .main_prompt_shared import _single_line
from .main_prompt_shared import prompt_section
from .main_prompt_shared import re
from .main_prompt_shared import render_prompt_sections
from .main_prompt_shared import runtime_persona_setting



class PrivateCompanionPluginPromptPart03Mixin:
    """PrivateCompanionPluginPromptPart03Mixin（从 PrivateCompanionPluginPromptMixin 拆出）。"""


    async def _append_conditional_tool_instructions_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        message_text = str(getattr(event, "message_str", "") or "")
        current_prompt = req.system_prompt or ""
        atrelay_section = self._atrelay_tool_prompt_section()
        atrelay_instruction = (
            render_prompt_sections(
                [atrelay_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(atrelay_section, PromptSection)
            else ""
        )
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        atrelay_marker = "<!-- private_companion_atrelay_tools_v1 -->"
        if atrelay_instruction and atrelay_marker not in current_prompt and atrelay_marker not in current_turn_prompt:
            if self._message_looks_like_atrelay_request(message_text):
                await self._append_atrelay_target_summary_to_request(event, req)
                current_prompt = req.system_prompt or ""
                current_turn_prompt = str(getattr(req, "prompt", "") or "")
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    atrelay_marker,
                    [atrelay_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="跨群转述工具注入",
                    key="tools.atrelay",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )
        relation_section = self._relation_lookup_prompt_section()
        relation_instruction = (
            render_prompt_sections(
                [relation_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(relation_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        relation_marker = "<!-- private_companion_relation_lookup_v1 -->"
        try:
            relation_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            relation_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        relation_query = any(token in message_text for token in ("查关系网", "关系网查", "查一下关系", "查查关系"))
        relation_query = relation_query or (
            any(token in message_text for token in ("查一下", "查查", "帮我查", "查一查"))
            and (
                bool(re.search(r"\d{5,12}", message_text))
                or any(token in message_text for token in ("这个人", "这人", "那个人", "那人", "是谁", "认识"))
            )
        )
        livingmemory_relation_context = (
            relation_private
            and bool(getattr(self, "enable_livingmemory_integration", False))
            and bool(getattr(self, "_livingmemory_available", lambda: False)())
        )
        if relation_private and relation_instruction and relation_marker not in current_prompt and relation_marker not in current_turn_prompt and (relation_query or livingmemory_relation_context):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                relation_marker,
                [relation_section],
                priority=87,
            )
            await self._record_request_prompt_fragment(
                event,
                title="关系网查询工具注入",
                key="tools.relation_lookup",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement, "触发原因": "livingmemory" if livingmemory_relation_context and not relation_query else "query"},
                section_manifest=manifest,
            )
        qzone_sections = self._qzone_tool_prompt_sections(event)
        qzone_instruction = render_prompt_sections(
            qzone_sections,
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        qzone_marker = "<!-- private_companion_qzone_tools_v1 -->"
        if qzone_instruction and qzone_marker not in current_prompt and qzone_marker not in current_turn_prompt:
            if any(token in message_text for token in ("说说", "空间", "QQ空间", "动态", "点赞", "评论")):
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    qzone_marker,
                    qzone_sections,
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="QQ 空间工具注入",
                    key="tools.qzone",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )
        schedule_management_section = self._schedule_management_tool_prompt_section()
        schedule_management_instruction = render_prompt_sections(
            [schedule_management_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        schedule_management_marker = "<!-- private_companion_schedule_management_v1 -->"
        try:
            schedule_management_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            schedule_management_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            schedule_management_private
            and self._can_manage_private_companion(event)
            and self._schedule_management_instruction_matches(message_text)
            and schedule_management_instruction
            and schedule_management_marker not in current_prompt
            and schedule_management_marker not in current_turn_prompt
        ):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                schedule_management_marker,
                [schedule_management_section],
                priority=88,
            )
            await self._record_request_prompt_fragment(
                event,
                title="指定日程管理工具注入",
                key="tools.schedule_management",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )
        memo_section = self._memo_management_tool_prompt_section()
        memo_instruction = render_prompt_sections(
            [memo_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        memo_marker = "<!-- private_companion_memo_management_v1 -->"
        try:
            memo_private = bool(getattr(event, "is_private_chat", lambda: False)())
            identity_for_event = getattr(self, "_event_permission_identity_id", None)
            memo_requester = (
                identity_for_event(event)
                if callable(identity_for_event)
                else self._permission_identity_id(event.get_sender_id())
            )
        except Exception:
            memo_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
            memo_requester = ""
        memo_owner = bool(memo_requester and self._is_private_companion_owner_user_id(memo_requester))
        memo_request = bool(
            memo_private
            and memo_owner
            and self._memo_management_instruction_matches(message_text)
        )
        if memo_request:
            self._mark_memo_request_tool_boundary(event, req)
            if self._remove_future_task_for_memo_request(req, message_text):
                logger.debug(
                    "明确便签请求已从初始工具集移除 future_task: session=%s",
                    _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                )
        if (
            memo_request
            and memo_instruction
            and memo_marker not in current_prompt
            and memo_marker not in current_turn_prompt
        ):
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                memo_marker,
                [memo_section],
                priority=88,
            )
            await self._record_request_prompt_fragment(
                event,
                title="备忘便签工具注入",
                key="tools.memo_management",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )

        creative_work_section = self._creative_work_tool_prompt_section()
        creative_work_instruction = (
            render_prompt_sections(
                [creative_work_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(creative_work_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        creative_work_marker = "<!-- private_companion_creative_work_tool_v1 -->"
        try:
            creative_work_private = bool(getattr(event, "is_private_chat", lambda: False)())
        except Exception:
            creative_work_private = ":FriendMessage:" in str(getattr(event, "unified_msg_origin", "") or "")
        if (
            creative_work_private
            and creative_work_instruction
            and self._creative_work_query_instruction_matches(message_text)
            and creative_work_marker not in current_prompt
            and creative_work_marker not in current_turn_prompt
        ):
            try:
                setattr(event, "private_companion_creative_work_tool_required", True)
            except Exception:
                pass
            placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                req,
                creative_work_marker,
                [creative_work_section],
                priority=89,
            )
            await self._record_request_prompt_fragment(
                event,
                title="创作正文读取工具注入",
                key="tools.creative_work",
                text=rendered_instruction,
                source="tools",
                mode="conditional",
                metadata={"注入位置": placement},
                section_manifest=manifest,
            )

        await self._append_media_delivery_truth_to_request(event, req)
        explicit_photo_request = self._photo_generation_instruction_matches(message_text)
        explicit_media_delivery_request = self._current_media_delivery_instruction_matches(message_text)
        referenced_media_edit_request = False
        if (
            not explicit_photo_request
            and self._referenced_media_edit_instruction_matches(message_text)
        ):
            finder = getattr(self, "_find_reply_image_sources_for_event", None)
            if callable(finder):
                try:
                    referenced_media_edit_request = bool(await finder(event))
                except Exception as exc:
                    logger.debug(
                        "引用图片编辑意图确认失败: session=%s error=%s",
                        _single_line(getattr(event, "unified_msg_origin", ""), 120) or "unknown",
                        _single_line(exc, 160),
                    )
        explicit_media_request = bool(
            explicit_photo_request
            or explicit_media_delivery_request
            or referenced_media_edit_request
        )
        reaction_expression_authorized = False
        if (
            not explicit_media_request
            and bool(runtime_persona_setting(self, 'enable_reaction_expression_experiment', False))
        ):
            reaction_expression_authorized = await self._preauthorize_reaction_expression_prompt(event)
        reaction_expression_evaluated = bool(
            self._reaction_expression_authorization(event)
        )
        allow_photo_on_reaction_turns = bool(
            runtime_persona_setting(
                self,
                'allow_generate_photo_on_reaction_turns',
                False,
            )
        )
        removed_reaction_tools = self._scope_reaction_media_tools_for_request(
            req,
            explicit_media_request=explicit_media_request,
            reaction_authorized=reaction_expression_authorized,
            reaction_evaluated=reaction_expression_evaluated,
            allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
        )
        if removed_reaction_tools:
            self._log_reaction_expression_event(
                event,
                stage="authorization",
                decision="scoped",
                reason="media_tools_scoped",
                scope=self._reaction_expression_scope(event),
            )
        photo_section = self._photo_generation_tool_prompt_section(
            event,
            include_spontaneous=reaction_expression_authorized,
            spontaneous_only=reaction_expression_authorized and not explicit_media_request,
            allow_photo_on_reaction_turns=allow_photo_on_reaction_turns,
        )
        photo_instruction = (
            render_prompt_sections(
                [photo_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(photo_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        photo_marker = "<!-- private_companion_photo_generation_tool_v1 -->"
        if photo_instruction and photo_marker not in current_prompt and photo_marker not in current_turn_prompt:
            if explicit_media_request or reaction_expression_authorized:
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    photo_marker,
                    [photo_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title=(
                        "实验性表情表达工具注入"
                        if reaction_expression_authorized and not explicit_media_request
                        else "生图工具注入"
                    ),
                    key=(
                        "tools.reaction_expression"
                        if reaction_expression_authorized and not explicit_media_request
                        else "tools.photo_generation"
                    ),
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={
                        "注入位置": placement,
                        "预授权": bool(reaction_expression_authorized),
                    },
                    section_manifest=manifest,
                )
        cross_user_section = self._cross_user_memory_query_prompt_section()
        cross_user_instruction = (
            render_prompt_sections(
                [cross_user_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            if isinstance(cross_user_section, PromptSection)
            else ""
        )
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        cross_user_marker = "<!-- private_companion_cross_user_memory_v1 -->"
        if cross_user_instruction and cross_user_marker not in current_prompt and cross_user_marker not in current_turn_prompt:
            if any(token in message_text for token in (
                "聊了什么", "说了什么", "发了什么", "讲了什么", "互动", "和谁聊", "跟谁聊", "最近跟", "最近和",
                "你和", "你跟", "在群里", "那个群", "这个群", "私聊过", "聊过",
            )):
                placement, rendered_instruction, manifest = self._place_conversation_prompt_sections(
                    req,
                    cross_user_marker,
                    [cross_user_section],
                    priority=88,
                )
                await self._record_request_prompt_fragment(
                    event,
                    title="跨用户记忆互通工具注入",
                    key="tools.cross_user_memory",
                    text=rendered_instruction,
                    source="tools",
                    mode="conditional",
                    metadata={"注入位置": placement},
                    section_manifest=manifest,
                )

    def _format_external_realtime_prompt_section(
        self,
        current_user: dict[str, Any] | None = None,
        *,
        public: bool = False,
    ) -> PromptSection:
        return prompt_section(
            key="realtime.activity_public" if public else "realtime.activity_continuity",
            title="实时共同活动公开状态" if public else "实时共同活动与短期连续性",
            source="external_realtime",
            content=self._format_external_realtime_context_body(
                current_user,
                public=public,
            ),
        )

    def _private_passive_state_update_prompt_sections(
        self,
        *,
        session: str,
        state: dict[str, Any],
        current_user: dict[str, Any] | None,
        inbound_text: str,
        lightweight: bool,
    ) -> tuple[list[PromptSection], bool, str]:
        session_key = _single_line(session, 160) or "unknown"
        cache = getattr(self, "_passive_state_session_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._passive_state_session_cache = cache
        fingerprint = self._private_passive_state_fingerprint(state, current_user)
        previous = cache.get(session_key) if isinstance(cache.get(session_key), dict) else {}
        changed = previous.get("fingerprint") != fingerprint
        third_party_activity_question = self._user_activity_question_targets_someone_else(inbound_text)
        direct_state_request = not third_party_activity_question and (
            self._user_asks_bot_current_state_or_activity(inbound_text)
            or self._user_asks_recent_bot_activity(inbound_text)
            or bool(
                re.search(r"(状态|日程|精力|心情|情绪|在干嘛|做什么|忙什么|近况)", str(inbound_text or ""))
            )
        )
        now_ts = _now_ts()
        cache[session_key] = {
            "fingerprint": fingerprint,
            "ts": now_ts,
            "last_changed_ts": now_ts if changed else _safe_float(previous.get("last_changed_ts"), now_ts),
        }
        if len(cache) > 240:
            stale = sorted(
                ((key, _safe_float(value.get("ts"), 0)) for key, value in cache.items() if isinstance(value, dict)),
                key=lambda item: item[1],
            )
            for key, _ in stale[: max(0, len(cache) - 200)]:
                cache.pop(key, None)
        if direct_state_request:
            state_section = self._format_private_passive_state_snapshot_section(
                state,
                current_user,
                direct=True,
            )
            state_changed = changed
            reason = "direct"
        elif changed:
            state_section = self._format_private_passive_state_snapshot_section(
                state,
                current_user,
                direct=False,
            )
            state_changed = True
            reason = "changed"
        elif bool(runtime_persona_setting(self, 'enable_passive_state_continuity_anchor', False)):
            state_section = self._format_private_passive_state_continuity_anchor_section(
                state,
                current_user,
            )
            state_changed = False
            reason = "continuity_anchor"
        else:
            return [], False, "unchanged_light" if lightweight else "unchanged"

        if reason == "continuity_anchor":
            reply_policy_section = self._private_passive_state_reply_policy_section(
                compact=True,
            )
            state_body = render_prompt_sections(
                [state_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            policy_body = render_prompt_sections(
                [reply_policy_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
            state_section = prompt_section(
                key=state_section.key,
                title=state_section.title,
                source=state_section.source,
                content=state_body[: max(0, 300 - len(policy_body) - 1)].rstrip(),
            )
        else:
            reply_policy_section = self._private_passive_state_reply_policy_section()
        sections = [state_section, reply_policy_section]
        return sections, state_changed, reason
