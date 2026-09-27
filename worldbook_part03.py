# -*- coding: utf-8 -*-
"""WorldbookPart03Mixin。

由 tools/split_mixin_domain.py 从 worldbook.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 486 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WorldbookMixin）。
"""
from __future__ import annotations

from .worldbook_shared import logger
from .worldbook_shared import Any
from .worldbook_shared import PromptDocument
from .worldbook_shared import PromptRenderMode
from .worldbook_shared import PromptSection
from .worldbook_shared import _now_ts
from .worldbook_shared import _safe_int
from .worldbook_shared import _single_line
from .worldbook_shared import prompt_document
from .worldbook_shared import prompt_section
from .worldbook_shared import prompt_text
from .worldbook_shared import re
from .worldbook_shared import render_prompt_document
from .worldbook_shared import render_prompt_sections
from .worldbook_shared import runtime_persona_setting



class WorldbookPart03Mixin:
    """WorldbookPart03Mixin（从 WorldbookMixin 拆出）。"""


    def _worldbook_self_registration_prompt_document(
        self,
        payload: dict[str, Any],
    ) -> PromptDocument:
        user_id = str(payload.get("user_id") or "").strip()
        name = _single_line(payload.get("name"), 40) or user_id
        aliases = payload.get("aliases") if isinstance(payload.get("aliases"), list) else []
        recent_lines = []
        recent_items = payload.get("recent") if isinstance(payload.get("recent"), list) else []
        for item in recent_items:
            if not isinstance(item, dict):
                continue
            speaker = _single_line(item.get("identity_name") or item.get("name"), 20) or "群友"
            msg = _single_line(item.get("text"), 80)
            if msg:
                recent_lines.append(f"- {speaker}: {msg}")
        intro = prompt_section(
            key="background.worldbook_registration.intro",
            title="关系网自登记人物印象",
            source="worldbook",
            content="请根据这条群聊自我介绍，生成一段适合“关系节点资料正文”的简短人物印象。",
        )
        fields = [
            prompt_section(
                key="background.worldbook_registration.persona",
                title="Bot 人格",
                source="worldbook",
                content=_single_line(self._get_default_persona_prompt(), 500),
            ),
            prompt_section(
                key="background.worldbook_registration.group",
                title="群号",
                source="worldbook",
                content=_single_line(payload.get("group_id"), 40),
            ),
            prompt_section(
                key="background.worldbook_registration.user",
                title="QQ",
                source="worldbook",
                content=user_id,
            ),
            prompt_section(
                key="background.worldbook_registration.name",
                title="自称/称呼",
                source="worldbook",
                content=(
                    name
                    + (
                        "；别名："
                        + "、".join(
                            _single_line(item, 24)
                            for item in aliases
                            if _single_line(item, 24)
                        )
                        if aliases
                        else ""
                    )
                ),
            ),
            prompt_section(
                key="background.worldbook_registration.introduction",
                title="自我介绍原文",
                source="worldbook",
                content=_single_line(payload.get("text"), 260),
            ),
            prompt_section(
                key="background.worldbook_registration.recent_group",
                title="附近群聊",
                source="worldbook",
                content="\n".join(recent_lines) or "（暂无）",
            ),
        ]
        requirements = prompt_section(
            key="background.worldbook_registration.requirements",
            title="输出要求",
            source="worldbook",
            content=(
                "要求：\n"
                "- 只输出 1 段中文，40 到 90 字\n"
                "- 像人物画像插件里的初始印象：描述可观察信息、称呼和互动注意点\n"
                "- 不要编造职业、性格、现实身份或私密事实\n"
                "- 不要写“根据聊天记录/资料显示/模型判断”"
            ),
        )
        root = prompt_section(
            key="background.worldbook_registration",
            title="关系网自登记人物印象任务",
            source="worldbook",
            content=prompt_text(
                render_prompt_sections([intro], mode=PromptRenderMode.BODY_ONLY),
                render_prompt_sections(fields, mode=PromptRenderMode.LABELED_BLOCK),
                render_prompt_sections([requirements], mode=PromptRenderMode.BODY_ONLY),
                separator="\n\n",
            ),
        )
        return prompt_document(user=[root])

    async def _refresh_worldbook_self_registration_impression(self, payload: dict[str, Any]) -> None:
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            return
        name = _single_line(payload.get("name"), 40) or user_id
        registration_document = self._worldbook_self_registration_prompt_document(payload)
        prompt = render_prompt_document(
            registration_document,
            mode=PromptRenderMode.BODY_ONLY,
        )["user"]
        impression = await self._llm_call(
            prompt,
            max_tokens=180,
            provider_id=self._task_provider(
                runtime_persona_setting(
                    self,
                    "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
                    getattr(self, "relationship_analysis_provider_id", ""),
                ),
                runtime_persona_setting(
                    self,
                    "MAI_STYLE_PROVIDER_ID",
                    getattr(self, "mai_style_provider_id", ""),
                ),
            ),
            task="worldbook_registration",
        )
        cleaned = _single_line(impression, 220)
        if not cleaned:
            cleaned = f"{name}在群里主动告诉 Bot 可以这样称呼自己；目前只有自我介绍信息，后续需要通过群聊慢慢补充印象。"
        async with self._data_lock:
            profile = self._worldbook_profile_by_user_id(user_id)
            if not isinstance(profile, dict) or not profile.get("auto_registration_pending"):
                return
            profile["content"] = cleaned
            if not profile.get("identity_note"):
                profile["identity_note"] = f"QQ {user_id}，自称{name}。"
            memories = profile.setdefault("important_memories", [])
            if isinstance(memories, list) and not memories:
                memories.append(
                    {
                        "title": "自我介绍",
                        "content": _single_line(payload.get("text"), 180),
                        "weight": 70,
                        "privacy": "internal",
                        "source": "群聊自登记",
                        "enabled": True,
                        "updated_at": _now_ts(),
                    }
                )
            profile["auto_registration_pending"] = False
            profile["auto_impression_ts"] = _now_ts()
            self._save_data_sync(sections={"worldbook_member_profiles"})
        logger.info("群聊关系网自登记印象已生成: user=%s name=%s", user_id, name)

    def _group_member_identity_label_for_token(self, group: dict[str, Any], token: str) -> str:
        query = re.sub(r"\s+", "", _single_line(token, 40))
        if not query:
            return ""
        members = group.get("members") if isinstance(group.get("members"), dict) else {}
        for user_id, member in members.items():
            if not isinstance(member, dict):
                continue
            candidates = [
                member.get("name"),
                member.get("identity_name"),
                member.get("display_name"),
                member.get("nickname"),
                member.get("card"),
            ]
            profile = self._worldbook_profile_by_user_id(str(user_id))
            if isinstance(profile, dict):
                candidates.extend([profile.get("name"), *(profile.get("aliases") or []), *(profile.get("observed_names") or [])])
            for candidate in candidates:
                value = re.sub(r"\s+", "", _single_line(candidate, 40))
                if value and value == query:
                    return self._group_member_identity_label(str(user_id), candidate, limit=24)
        return ""

    def _worldbook_profile_tokens(
        self,
        profile: dict[str, Any],
        *,
        include_observed: bool = True,
    ) -> list[str]:
        tokens: list[str] = []
        raw_tokens = [profile.get("name"), *(profile.get("aliases") or [])]
        if include_observed:
            raw_tokens.extend(profile.get("observed_names") or [])
        for token in raw_tokens:
            token = _single_line(token, 40)
            if token and token not in tokens:
                tokens.append(token)
        for raw_id in (profile.get("linked_qq_user_id"), profile.get("user_id")):
            user_id = _single_line(raw_id, 40)
            if user_id and user_id not in tokens:
                tokens.append(user_id)
        return tokens

    def _worldbook_claimed_other_identity(self, sender_id: str, text: str) -> dict[str, str]:
        """Describe an explicit self-claim that belongs to another stable QQ profile."""
        current_sender_id = _single_line(sender_id, 40)
        cleaned = _single_line(text, 120)
        if not current_sender_id or not cleaned:
            return {}
        match = re.match(
            r"^(?:我是|我叫|叫我|以后叫我|可以叫我|你可以叫我)([^。！？!?\n]{1,32})",
            cleaned,
        )
        if not match:
            return {}
        claimed = re.sub(r"[\s，,、~～]+$", "", str(match.group(1) or "").strip())
        claimed_key = re.sub(r"\s+", "", claimed).casefold()
        if not claimed_key:
            return {}
        profiles = self.data.get("worldbook_member_profiles") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(profiles, dict):
            return {}
        for profile_user_id, profile in profiles.items():
            target_user_id = _single_line(profile_user_id, 40)
            if target_user_id == current_sender_id or not isinstance(profile, dict) or not profile.get("enabled", True):
                continue
            for token in sorted(
                self._worldbook_profile_tokens(profile, include_observed=False),
                key=len,
                reverse=True,
            ):
                token_key = re.sub(r"\s+", "", token).casefold()
                if token_key and token_key == claimed_key:
                    return {
                        "user_id": target_user_id,
                        "name": _single_line(profile.get("name"), 40) or token,
                        "claimed": claimed,
                    }
        return {}

    @staticmethod
    def _worldbook_token_usable(token: str) -> bool:
        token = _single_line(token, 40)
        if not token:
            return False
        if token.isdigit():
            return len(token) >= 5
        if len(token) < 2:
            return False
        if token in {"群友", "老师", "同学", "朋友", "bot", "Bot", "BOT", "我", "你", "他", "她", "它"}:
            return False
        return True

    def _worldbook_token_mentioned(self, token: str, text: str) -> bool:
        token = _single_line(token, 40)
        text = str(text or "")
        if not token or not text:
            return False
        if token.isdigit():
            return token in text
        if not self._worldbook_token_usable(token):
            return False
        if len(token) <= 2:
            pattern = rf"(?<![\u4e00-\u9fffA-Za-z0-9_]){re.escape(token)}(?![\u4e00-\u9fffA-Za-z0-9_])"
            return bool(re.search(pattern, text))
        return token in text

    def _worldbook_token_mentioned_in_private_hint(self, token: str, text: str) -> bool:
        token = _single_line(token, 40)
        text = str(text or "")
        if self._worldbook_token_mentioned(token, text):
            return True
        if not token or token.isdigit() or len(token) > 2 or not self._worldbook_token_usable(token):
            return False
        # “帮我找某个两字昵称你认识吗”这类短昵称后面常直接接动词，
        # 明确问人或转述时不能沿用普通闲聊的强边界，否则会漏掉目标。
        before = r"(^|[\s，,。？?！!：:、@和跟找叫给对向把让问找一下])"
        after = r"(?=(你认识|认识|认得|知道|是谁|是|说|发|问|找|叫|踢|$|[\s，,。？?！!：:、]))"
        return bool(re.search(before + re.escape(token) + after, text))

    def _worldbook_profile_view(
        self,
        profile: dict[str, Any],
        *,
        match_reason: str,
        confidence: str,
        scope: str = "mentioned",
    ) -> dict[str, Any]:
        view = dict(profile)
        view["_match_reason"] = match_reason
        view["_match_confidence"] = confidence
        view["_match_scope"] = scope
        return view

    def _worldbook_private_token_hits(
        self,
        text: str,
    ) -> dict[str, list[tuple[str, dict[str, Any], str]]]:
        """Return private-chat name/alias hits grouped by normalized token."""
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return {}
        token_hits: dict[str, list[tuple[str, dict[str, Any], str]]] = {}
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict) or not profile.get("enabled", True):
                continue
            for token in self._worldbook_profile_tokens(profile, include_observed=False):
                if self._worldbook_token_mentioned_in_private_hint(token, text):
                    token_key = re.sub(r"\s+", "", token).casefold()
                    if token_key:
                        token_hits.setdefault(token_key, []).append((str(user_id), profile, token))
        return token_hits

    def _select_worldbook_member_profiles_for_private_text(self, text: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        text = str(text or "")
        if not text:
            return []
        configured_limit = max(1, _safe_int(runtime_persona_setting(self, "worldbook_member_inject_limit", 6), 6, 1))
        max_items = max(1, min(8, _safe_int(limit, min(configured_limit, 4), 1)))
        selected: dict[str, dict[str, Any]] = {}
        for match in re.findall(r"\d{5,12}", text):
            profile = self._worldbook_profile_by_user_id(match)
            if isinstance(profile, dict) and profile.get("enabled", True):
                selected[match] = self._worldbook_profile_view(
                    profile,
                    match_reason="当前私聊消息提到 QQ 号",
                    confidence="mentioned",
                )
        # A private message has no group roster to disambiguate a name.  Keep
        # historical observed names out of this global lookup, and suppress a
        # token entirely when it belongs to more than one stable user.
        token_hits = self._worldbook_private_token_hits(text)
        for token_key, hits in token_hits.items():
            if len({item[0] for item in hits}) != 1:
                continue
            user_id, profile, token = hits[0]
            if len(selected) >= max_items:
                break
            if user_id in selected:
                continue
            selected[user_id] = self._worldbook_profile_view(
                        profile,
                        match_reason=f"当前私聊消息提到：{token}",
                        confidence="mentioned",
                    )
        ranked = sorted(
            selected.values(),
            key=lambda item: _safe_int(item.get("priority"), 120, -1000),
            reverse=True,
        )
        return ranked[:max_items]

    def _format_worldbook_private_mentions_for_prompt(
        self,
        text: str,
        *,
        limit: int | None = None,
    ) -> str:
        section = self._format_worldbook_private_mentions_prompt_section(
            text,
            limit=limit,
        )
        return render_prompt_sections(
            [section],
            mode=PromptRenderMode.LABELED_BLOCK,
        )

    def _format_worldbook_private_mentions_prompt_section(
        self,
        text: str,
        *,
        limit: int | None = None,
    ) -> PromptSection:
        profiles = self._select_worldbook_member_profiles_for_private_text(text, limit=limit)
        token_hits = self._worldbook_private_token_hits(text)
        ambiguous_tokens = sorted(
            {hits[0][2] for hits in token_hits.values() if len({item[0] for item in hits}) > 1},
            key=len,
            reverse=True,
        )[:8]
        lines: list[str] = []
        if ambiguous_tokens:
            lines.append(
                "- 称呼线索存在多个稳定用户："
                + "、".join(str(token) for token in ambiguous_tokens)
                + "。不能仅凭这个称呼判断对象；需要时先自然澄清，不要套用任何一人的关系、记忆或权限。"
            )
        injected = []
        for profile in profiles:
            profile_uid = _single_line(profile.get("user_id"), 40)
            name = _single_line(profile.get("name"), 40) or profile_uid or "-"
            aliases = "、".join(
                token
                for token in self._worldbook_profile_tokens(profile, include_observed=False)[:6]
                if token != profile_uid and token != name
            )
            identity = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 140)
            boundary = _single_line(profile.get("boundary_note"), 80)
            parts = [f"{name}（QQ:{profile_uid or '-'}）"]
            if aliases:
                parts.append(f"称呼线索：{aliases}")
            if identity:
                parts.append(f"身份：{identity}")
            if boundary:
                parts.append(f"边界：{boundary}")
            lines.append("- " + "｜".join(parts))
            injected.append(f"{profile_uid}:{name}")
        if lines:
            logger.info("本轮提及关系网对象注入: users=%s", "；".join(injected))
        return prompt_section(
            key="worldbook.private_mentions",
            title="本轮提到的关系网对象",
            source="worldbook",
            content="\n".join(lines),
        )

    def _select_worldbook_member_profiles_for_group(
        self,
        group: dict[str, Any],
        *,
        sender_id: str = "",
        text: str = "",
    ) -> list[dict[str, Any]]:
        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        text = str(text or "")
        recent_speaker_ids: list[str] = []
        recent = group.get("recent_messages")
        if isinstance(recent, list):
            for item in recent[-6:]:
                if not isinstance(item, dict):
                    continue
                recent_id = _single_line(item.get("sender_id"), 40)
                if recent_id and recent_id not in recent_speaker_ids:
                    recent_speaker_ids.append(recent_id)
        selected: dict[str, dict[str, Any]] = {}
        if sender_id and isinstance(profiles.get(sender_id), dict) and profiles[sender_id].get("enabled", True):
            selected[sender_id] = self._worldbook_profile_view(
                profiles[sender_id],
                match_reason="当前发言者 QQ 精确匹配",
                confidence="confirmed",
                scope="current_sender",
            )
        member_limit = max(1, _safe_int(runtime_persona_setting(self, "worldbook_member_inject_limit", 6), 6, 1))
        if runtime_persona_setting(self, "worldbook_member_match_aliases", True) and text and len(selected) < member_limit:
            token_hits: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for user_id, profile in profiles.items():
                if not isinstance(profile, dict) or not profile.get("enabled", True):
                    continue
                if user_id in selected:
                    continue
                for token in self._worldbook_profile_tokens(profile):
                    if self._worldbook_token_mentioned(token, text) or self._worldbook_token_mentioned_in_private_hint(token, text):
                        token_hits.setdefault(token, []).append((str(user_id), profile))
            for token in sorted(token_hits, key=len, reverse=True):
                hits = token_hits[token]
                if len(hits) != 1:
                    continue
                user_id, profile = hits[0]
                if user_id in selected:
                    continue
                selected[user_id] = self._worldbook_profile_view(
                    profile,
                    match_reason=f"当前消息明确提到：{token}",
                    confidence="mentioned",
                    scope="mentioned",
                )
                if len(selected) >= member_limit:
                    break
        for recent_id in recent_speaker_ids:
            if len(selected) >= member_limit:
                break
            if recent_id in selected:
                continue
            if self._is_target_private_user(recent_id, profiles.get(recent_id) if isinstance(profiles.get(recent_id), dict) else None):
                continue
            profile = profiles.get(recent_id)
            if isinstance(profile, dict) and profile.get("enabled", True):
                selected[recent_id] = self._worldbook_profile_view(
                    profile,
                    match_reason="最近发言者 QQ 精确匹配",
                    confidence="context",
                    scope="recent_speaker",
                )
        ranked = sorted(
            selected.values(),
            key=lambda item: (
                {
                    "current_sender": 3,
                    "mentioned": 2,
                    "recent_speaker": 1,
                }.get(str(item.get("_match_scope") or ""), 0),
                1 if item.get("_match_confidence") == "confirmed" else 0,
                _safe_int(item.get("priority"), 120, -1000),
            ),
            reverse=True,
        )
        return ranked[:member_limit]
