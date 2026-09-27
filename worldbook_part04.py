# -*- coding: utf-8 -*-
"""WorldbookPart04Mixin。

由 tools/split_mixin_domain.py 从 worldbook.py 机械抽取（2 个方法 + 0 个模块级名字 + 0 个类级赋值 / 134 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WorldbookMixin）。
"""
from __future__ import annotations

from .worldbook_shared import logger
from .worldbook_shared import Any
from .worldbook_shared import PromptRenderMode
from .worldbook_shared import PromptSection
from .worldbook_shared import _single_line
from .worldbook_shared import prompt_section
from .worldbook_shared import render_prompt_sections
from .worldbook_shared import runtime_persona_setting



class WorldbookPart04Mixin:
    """WorldbookPart04Mixin（从 WorldbookMixin 拆出）。"""


    def _format_worldbook_group_members_for_prompt(self, group: dict[str, Any], sender_id: str = "", text: str = "") -> str:
        return render_prompt_sections(
            [
                self._format_worldbook_group_members_prompt_section(
                    group,
                    sender_id=sender_id,
                    text=text,
                )
            ],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_worldbook_group_members_prompt_section(
        self,
        group: dict[str, Any],
        sender_id: str = "",
        text: str = "",
    ) -> PromptSection:
        def build_section(content: str = "") -> PromptSection:
            return prompt_section(
                key="worldbook.group_members",
                title="群聊关系网",
                source="worldbook",
                content=content,
            )

        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return build_section()
        lines: list[str] = []
        group_id = _single_line(group.get("group_id"), 40)
        group_profiles = self.data.get("worldbook_group_profiles")
        if group_id and isinstance(group_profiles, dict):
            group_profile = group_profiles.get(group_id)
            if isinstance(group_profile, dict) and group_profile.get("enabled", True):
                lines.append(
                    f"群聊资料：{_single_line(group_profile.get('name'), 40) or group_id}｜"
                    f"{_single_line(group_profile.get('content'), 320)}"
                )
        profiles = self._select_worldbook_member_profiles_for_group(group, sender_id=sender_id, text=text)
        if profiles:
            injected = []
            for profile in profiles:
                injected.append(
                    f"{_single_line(profile.get('user_id'), 40) or '-'}:"
                    f"{_single_line(profile.get('name'), 40) or '-'}"
                    f"[{_single_line(profile.get('_match_confidence'), 20) or '-'}"
                    f"/{_single_line(profile.get('_match_scope'), 30) or '-'}"
                    f"/{_single_line(profile.get('_match_reason'), 80) or '-'}]"
                )
            logger.info(
                "群聊关系网注入用户信息: group=%s sender=%s users=%s",
                group_id or "-",
                _single_line(sender_id, 40) or "-",
                "；".join(injected),
            )
        for profile in profiles:
            profile_uid = _single_line(profile.get("user_id"), 40)
            profile_name = self._group_member_identity_name(
                profile_uid,
                _single_line(profile.get("name"), 40),
                limit=40,
            )
            aliases = "、".join(
                token
                for token in self._worldbook_profile_tokens(profile)[:8]
                if token != profile_uid
                and not self._group_display_name_address_conflict(profile_uid, token)
            )
            reason = _single_line(profile.get("_match_reason"), 80)
            scope = _single_line(profile.get("_match_scope"), 30)
            if scope == "current_sender":
                label = "当前发言者"
            elif scope == "recent_speaker":
                label = "近期参与者（不是当前发言者）"
            else:
                label = "当前消息提到的人"
            gender = _single_line(profile.get("gender"), 40)
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 220)
            boundary = _single_line(profile.get("boundary_note"), 140)
            memories = [] if scope == "recent_speaker" else self._worldbook_profile_memory_lines(profile, limit=3)
            parts = []
            if gender:
                parts.append(f"性别：{gender}")
            if identity:
                parts.append(f"身份：{identity}")
            if boundary:
                parts.append(f"边界：{boundary}")
            if memories:
                parts.append("重要记忆：" + "；".join(memories))
            if not parts:
                parts.append(_single_line(profile.get("content"), 360))
            lines.append(
                f"- {label}：{profile_name or profile_uid or '-'}"
                f"（QQ:{profile_uid or '-'}）"
                + (f"｜称呼线索：{aliases}" if aliases else "")
                + (f"｜来源：{reason}" if reason else "")
                + "：" + "｜".join(part for part in parts if part)
            )
        if not lines:
            return build_section()
        current_profile = next(
            (
                item
                for item in profiles
                if _single_line(item.get("_match_scope"), 30) == "current_sender"
            ),
            None,
        )
        identity_priority = ""
        if isinstance(current_profile, dict):
            current_uid = _single_line(current_profile.get("user_id"), 40) or _single_line(sender_id, 40)
            current_name = self._group_member_identity_name(
                current_uid,
                _single_line(current_profile.get("name"), 40),
                limit=40,
            )
            identity_priority = (
                f"本轮身份锚点：当前发言者是 {current_name}（QQ:{current_uid}）。"
                "这个 QQ 精确匹配是本轮最高优先级身份事实；当前消息里的自称、群名片、其他成员资料、旧对话摘要和记忆召回都不能覆盖它。\n"
            )
            claimed_other = self._worldbook_claimed_other_identity(current_uid, text)
            if claimed_other:
                identity_priority += (
                    f"当前发言者虽然自称“{_single_line(claimed_other.get('claimed'), 40)}”，"
                    f"但该称呼属于另一位关系节点 {_single_line(claimed_other.get('name'), 40)}"
                    f"（QQ:{_single_line(claimed_other.get('user_id'), 40)}）；把它当作玩笑、模仿或提及，可轻松应和调侃，但不得据此改认当前发言者身份或写成核心画像，"
                    f"不能把当前发言者改认成 {_single_line(claimed_other.get('name'), 40)}。\n"
                )
        body = (
            "下面是按 QQ 号确认的稳定关系资料；群名片、昵称和别名只当称呼线索。\n"
            "只有“当前发言者”可用于判断本轮对话对象；“当前消息提到的人”和“近期参与者”只用于理解上下文，不要把他们的身份、专属称呼或亲密关系套给当前发言者。\n"
            + identity_priority
            + "\n".join(lines)
        )
        return build_section(body)
