# -*- coding: utf-8 -*-
"""PrivateCompanionPluginPromptPart04Mixin。

由 tools/split_mixin_domain.py 从 main_prompt.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 462 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPluginPromptMixin）。
"""
from __future__ import annotations

from .main_prompt_shared import logger
from .main_prompt_shared import Any
from .main_prompt_shared import AstrMessageEvent
from .main_prompt_shared import DELIVERY_GROUP_MARKER_METADATA_KEY
from .main_prompt_shared import PLACEMENT_DYNAMIC_SYSTEM
from .main_prompt_shared import PromptRenderMode
from .main_prompt_shared import PromptSection
from .main_prompt_shared import ProviderRequest
from .main_prompt_shared import _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES
from .main_prompt_shared import _single_line
from .main_prompt_shared import prompt_section
from .main_prompt_shared import render_prompt_sections
from .main_prompt_shared import runtime_persona_setting



class PrivateCompanionPluginPromptPart04Mixin:
    """PrivateCompanionPluginPromptPart04Mixin（从 PrivateCompanionPluginPromptMixin 拆出）。"""


    async def _append_group_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        group_id: str,
    ) -> str:
        if not group_id:
            return ""
        try:
            state = await self._ensure_daily_state(
                skip_conversation_summary=True,
                passive_fast=True,
            )
            boundary_section = self._format_active_period_boundary_prompt_section(
                state,
                public=True,
            )
            boundary = render_prompt_sections(
                [boundary_section],
                mode=PromptRenderMode.BODY_ONLY,
            )
        except Exception as exc:
            logger.debug(
                "群聊读取经期互动边界失败，已跳过: group=%s error=%s",
                _single_line(group_id, 40) or "-",
                _single_line(exc, 120),
            )
            return ""
        if not boundary:
            return ""

        marker = "<!-- private_companion_period_boundary_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            boundary_section,
            priority=89,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="group",
            priority=89,
            metadata={"注入位置": placement, "群号": _single_line(group_id, 40)},
        )
        return boundary

    async def _append_private_active_period_boundary_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        state: dict[str, Any],
    ) -> str:
        boundary_section = self._format_active_period_boundary_prompt_section(
            state,
            public=False,
        )
        boundary = render_prompt_sections(
            [boundary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if not boundary:
            return ""
        marker = "<!-- private_companion_period_boundary_v1 -->"
        if self._request_has_managed_prompt_marker(req, marker):
            return boundary
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            boundary_section,
            priority=89,
        )
        await self._record_request_prompt_fragment(
            event,
            title="私聊经期互动边界",
            key="state.period_boundary",
            text=boundary,
            source="daily_state",
            mode="private",
            priority=89,
            metadata={"注入位置": placement},
        )
        return boundary

    def _format_group_persona_denoise_prompt_sections(
        self,
        event: AstrMessageEvent | None = None,
    ) -> list[PromptSection]:
        body = self._format_group_persona_denoise_body(event)
        if not body:
            return []
        return [
            prompt_section(
                key="group.persona_denoise",
                title="群聊人格降噪",
                source="group",
                content=body,
            ),
            prompt_section(
                key="group.persona_denoise.joke_boundary",
                title="群聊玩笑边界",
                source="group",
                content=self._group_persona_denoise_joke_boundary(),
            ),
        ]

    async def _append_group_persona_denoise_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not bool(runtime_persona_setting(self, 'enable_group_companion', True)):
            return
        group_id = self._extract_group_id_from_event(event)
        if not group_id or not self._group_enabled_for_event(group_id):
            return
        denoise_sections = self._format_group_persona_denoise_prompt_sections(event)
        if not denoise_sections:
            return
        denoise_text = render_prompt_sections(denoise_sections)
        marker = "<!-- private_companion_group_persona_denoise_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement, _, _ = self._place_conversation_prompt_sections(
            req,
            marker,
            denoise_sections,
            priority=32,
        )
        await self._record_request_prompt_fragment(
            event,
            title="群聊人格降噪注入",
            key="group.persona_denoise",
            text=denoise_text,
            source="group",
            mode="group",
            metadata={"注入位置": placement},
        )

    async def _append_non_target_private_identity_guard_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        marker = "<!-- private_companion_non_target_private_guard_v1 -->"
        current_prompt = req.system_prompt or ""
        if marker in current_prompt:
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = ""
        user_id = _single_line(user_id, 40)
        resolver = getattr(self, "_private_user_id_for_event", None)
        canonical_user_id = (
            resolver(event, user_id)
            if callable(resolver) and user_id
            else self._canonical_private_user_id(user_id)
            if user_id
            else ""
        )
        if canonical_user_id:
            user_id = canonical_user_id
        if not user_id or self._is_bot_self_user_id(user_id):
            return
        raw_users = self.data.get("users", {})
        current_user = raw_users.get(user_id) if isinstance(raw_users, dict) else None
        if (
            isinstance(current_user, dict)
            and self._private_passive_profile_available(user_id, current_user)
        ):
            return
        display_name = ""
        try:
            display_name = _single_line(self._sender_display_name(event), 40)
        except Exception:
            display_name = ""
        lines = [
            f"当前私聊对象稳定 ID：{user_id}",
            "这个用户不是插件当前启用的目标陪伴用户/主用户。",
            "如果基础人格里包含“主要用户/主人”“恋人”“专属称呼”或只属于主要用户的关系设定,不要套用到当前私聊对象身上。",
            "可以保留人格的通用说话风格,但关系身份、亲密度、记忆和承诺必须按当前用户重新判断。",
            "除非当前用户明确提出角色扮演或临时设定,否则不要把对方当成主要用户、恋人或目标陪伴对象。",
        ]
        if display_name and display_name != user_id:
            lines.append(f"平台当前显示名：{display_name}。显示名只作称呼线索,不能覆盖稳定 ID。")
        profile = None
        try:
            profile = self._worldbook_profile_by_user_id(user_id)
        except Exception:
            profile = None
        profile_lines: list[str] = []
        if isinstance(profile, dict) and profile.get("enabled", True):
            name = _single_line(profile.get("name"), 40)
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            aliases = []
            for item in profile.get("aliases") if isinstance(profile.get("aliases"), list) else []:
                alias = _single_line(item, 24)
                if alias and alias != user_id and alias not in aliases:
                    aliases.append(alias)
            profile_lines.append("以下资料来自当前私聊 QQ 号的精确匹配,只用于识别当前用户,不能外推到主用户。")
            if name and name != user_id:
                profile_lines.append(f"登记名：{name}")
            if gender:
                profile_lines.append(f"性别：{gender}")
            if aliases:
                profile_lines.append(f"可用称呼线索：{'、'.join(aliases[:6])}")
            if identity:
                profile_lines.append(f"身份备注：{identity}")
            if boundary:
                profile_lines.append(f"互动边界：{boundary}")
            profile_lines.append("即使此用户资料中有亲昵称呼,也必须服从上面的防串规则：不要把目标陪伴用户的专属关系套给 TA。")
        guard_sections = [
            prompt_section(
                key="identity.non_target_private",
                title="私聊身份防串",
                source="identity",
                content=chr(10).join(lines),
            )
        ]
        if profile_lines:
            guard_sections.append(
                prompt_section(
                    key="identity.non_target_profile",
                    title="当前用户关系网资料",
                    source="identity",
                    content=chr(10).join(profile_lines),
                )
            )
        guard_text = render_prompt_sections(guard_sections)
        for index, section in enumerate(guard_sections):
            self._materialize_conversation_system_block(
                req,
                section=section,
                marker=marker if index == 0 else "",
                priority=10,
                placement=PLACEMENT_DYNAMIC_SYSTEM,
                metadata={DELIVERY_GROUP_MARKER_METADATA_KEY: marker},
            )
        await self._record_request_prompt_fragment(
            event,
            title="非目标私聊防串注入",
            key="identity.non_target",
            text=guard_text,
            source="identity",
            mode="private",
        )

    def _format_atrelay_target_summary_prompt_section(
        self,
        text: str,
    ) -> PromptSection | None:
        if not (self.enabled and bool(getattr(self, "enable_atrelay_tools", False))):
            return None
        text = str(text or "")
        if not self._message_looks_like_atrelay_request(text):
            return None
        lines: list[str] = []
        has_signal = False
        group_expected = any(token in text for token in ("群里", "群聊", "发到", "发群", "群"))
        member_expected = any(token in text for token in ("找", "告诉", "转告", "转达", "跟", "和", "给", "@", "艾特", "私聊", "说一句", "说一声"))

        group_matches = self._atrelay_cached_group_matches(text)
        if group_matches:
            has_signal = True
            if len(group_matches) == 1:
                group = group_matches[0]
                lines.append(
                    "目标群候选：确定｜"
                    f"{_single_line(group.get('group_name'), 60) or group.get('group_id')}（群号:{_single_line(group.get('group_id'), 40)}）"
                    f"｜来源:{_single_line(group.get('source'), 30) or 'local'}"
                )
            else:
                parts = [
                    f"{_single_line(item.get('group_name'), 40) or item.get('group_id')}（{_single_line(item.get('group_id'), 40)}）"
                    for item in group_matches[:5]
                ]
                lines.append("目标群候选：多个｜" + "；".join(parts))
        elif group_expected:
            has_signal = True
            lines.append("目标群候选：未命中｜用户可能还需要补充群名或群号。")

        member_profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=5)
        if member_profiles:
            has_signal = True
            if len(member_profiles) == 1:
                profile = member_profiles[0]
                uid = _single_line(profile.get("user_id"), 40)
                name = _single_line(profile.get("name"), 40) or uid
                identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 100)
                parts = [f"{name}（QQ:{uid or '-'}）"]
                if identity:
                    parts.append(f"身份:{identity}")
                lines.append("目标成员候选：确定｜" + "｜".join(parts))
            else:
                parts = [
                    f"{_single_line(profile.get('name'), 32) or _single_line(profile.get('user_id'), 40)}"
                    f"（{_single_line(profile.get('user_id'), 40) or '-'}）"
                    for profile in member_profiles[:5]
                ]
                lines.append("目标成员候选：多个｜" + "；".join(parts))
        elif member_expected:
            has_signal = True
            lines.append("目标成员候选：未命中｜没有从关系网里确定收话人。")

        if not has_signal:
            return None
        lines.append("这些只是本轮目标解析线索；真正发送仍以用户明确要求和工具执行结果为准。")
        return prompt_section(
            key="atrelay.target_summary",
            title="本轮转述目标摘要",
            source="atrelay",
            content="\n".join(lines),
        )

    async def _append_atrelay_target_summary_to_request(self, event: AstrMessageEvent, req: ProviderRequest) -> bool:
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        summary_section = self._format_atrelay_target_summary_prompt_section(text)
        if summary_section is None:
            return False
        summary = render_prompt_sections(
            [summary_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        marker = "<!-- private_companion_atrelay_target_summary_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return True
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            summary_section,
            priority=86,
        )
        await self._record_request_prompt_fragment(
            event,
            title="本轮转述目标摘要",
            key="tools.atrelay.targets",
            text=summary,
            source="tools",
            mode="conditional",
            metadata={"注入位置": placement},
            section_manifest=[summary_section],
        )
        return True

    async def _append_worldbook_mentions_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        *,
        mode: str = "conditional",
    ) -> None:
        if not bool(runtime_persona_setting(self, 'enable_worldbook_member_recognition', False)):
            return
        text = str(
            getattr(event, "private_companion_group_text", "")
            or getattr(event, "message_str", "")
            or ""
        )
        if self._format_atrelay_target_summary_prompt_section(text) is not None:
            return
        mention_section = self._format_worldbook_private_mentions_prompt_section(
            text,
            limit=4,
        )
        mention_text = render_prompt_sections(
            [mention_section],
            mode=PromptRenderMode.BODY_ONLY,
        )
        if not mention_text:
            return
        marker = "<!-- private_companion_worldbook_mentions_v1 -->"
        current_prompt = req.system_prompt or ""
        current_turn_prompt = str(getattr(req, "prompt", "") or "")
        if marker in current_prompt or marker in current_turn_prompt:
            return
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            mention_section,
            priority=58,
        )
        await self._record_request_prompt_fragment(
            event,
            title="本轮关系网提及注入",
            key="worldbook.mentions",
            text=mention_text,
            source="worldbook",
            mode=mode,
            metadata={"注入位置": placement},
            section_manifest=[mention_section],
        )

    async def _append_rest_reply_backlog_to_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        user: dict[str, Any],
    ) -> str:
        backlog_prompt = self._take_rest_reply_backlog_prompt(user)
        if not backlog_prompt:
            return ""
        marker = "<!-- private_companion_rest_backlog_v1 -->"
        backlog_section = prompt_section(
            key="rest.backlog",
            title="醒后补看私聊",
            source="daily_state",
            content=backlog_prompt,
        )
        placement = self._place_conversation_prompt_section(
            req,
            marker,
            backlog_section,
            priority=25,
        )
        await self._record_request_prompt_fragment(
            event,
            title="醒后补看私聊",
            key="rest.backlog",
            text=backlog_prompt,
            source="daily_state",
            mode="private",
            metadata={"注入位置": placement},
        )
        return backlog_prompt

    def _normalize_proactive_only_unlock_key(self, value: Any) -> str:
        text = _single_line(value, 80).strip()
        if not text:
            return ""
        return _PROACTIVE_ONLY_TEMP_UNLOCK_ALIASES.get(text, text)

    async def _append_proactive_only_unlocked_llm_request_fragments(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if self._proactive_only_temp_unlock_allows("enable_tts_enhancement"):
            await self.apply_tts_enhancement_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_forward_message_adaptation"):
            await self._append_forward_message_context_to_request(event, req)
        if self._proactive_only_temp_unlock_allows("enable_environment_perception"):
            await self._append_environment_perception_to_request(event, req)

    def _format_proactive_only_temp_unlocks(self) -> str:
        unlocks = self._proactive_only_unlock_store()
        if not unlocks:
            return "当前没有临时放行项。"
        labels = [self._proactive_only_unlock_label(key) for key in sorted(unlocks)]
        return "当前主动专用模式临时放行：\n" + "\n".join(f"- {label}" for label in labels)

    def _apply_proactive_only_temp_unlock(self, key: str, *, sync_related: bool = False, clear: bool = False) -> str:
        normalized = self._normalize_proactive_only_unlock_key(key)
        if not normalized:
            return "没有识别到要临时放行的功能。"
        keys = self._proactive_only_unlock_store()
        target_keys = {normalized}
        if sync_related:
            target_keys.update(self._related_proactive_only_unlock_keys(normalized))
        if clear:
            removed = keys & target_keys
            keys.difference_update(target_keys)
            self._set_proactive_only_unlock_store(keys)
            self._save_data_sync(sections={"proactive_only_temp_unlocks"})
            if not removed:
                return "对应临时放行项本来就没有开启。"
            return "已取消临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(removed))
        keys.update(target_keys)
        self._set_proactive_only_unlock_store(keys)
        self._save_data_sync(sections={"proactive_only_temp_unlocks"})
        return "已临时放行：\n" + "\n".join(f"- {self._proactive_only_unlock_label(item)}" for item in sorted(target_keys))
