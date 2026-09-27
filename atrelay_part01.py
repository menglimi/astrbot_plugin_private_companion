# -*- coding: utf-8 -*-
"""AtRelayPart01Mixin。

由 tools/split_mixin_domain.py 从 atrelay.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 480 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 AtRelayMixin）。
"""
from __future__ import annotations

from .atrelay_shared import _render_atrelay_prompt_section_labeled, logger
from .atrelay_shared import Any
from .atrelay_shared import AstrMessageEvent
from .atrelay_shared import LLMResponse
from .atrelay_shared import Plain
from .atrelay_shared import PromptRenderMode
from .atrelay_shared import PromptSection
from .atrelay_shared import _now_ts
from .atrelay_shared import _safe_float
from .atrelay_shared import _safe_int
from .atrelay_shared import _single_line
from .atrelay_shared import prompt_section
from .atrelay_shared import re
from .atrelay_shared import render_prompt_sections
from .atrelay_shared import runtime_persona_setting
from .atrelay_shared import filter



class AtRelayPart01Mixin:
    """AtRelayPart01Mixin（从 AtRelayMixin 拆出）。"""


    def _atrelay_event_user_id(self, event: AstrMessageEvent | None) -> str:
        """Resolve the current sender inside its platform/account boundary."""
        if event is None:
            return ""
        try:
            raw_id = _single_line(event.get_sender_id(), 160)
        except Exception:
            raw_id = ""
        if not raw_id:
            return ""
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                resolved = _single_line(resolver(event, raw_id), 160)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        try:
            return _single_line(self._canonical_private_user_id(raw_id), 160) or raw_id
        except Exception:
            return raw_id

    async def _get_group_member_list_for_tool(
        self,
        event: AstrMessageEvent,
        group_id: str,
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        now = _now_ts()
        cache = getattr(self, "_atrelay_member_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._atrelay_member_cache = cache
        cached = cache.get(group_id)
        if (
            not force_refresh
            and isinstance(cached, dict)
            and now - _safe_float(cached.get("ts"), 0) < self.atrelay_member_cache_minutes * 60
        ):
            members = cached.get("items")
            if isinstance(members, list):
                return [dict(item) for item in members if isinstance(item, dict)]
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return []
        raw_members = await call_action("get_group_member_list", group_id=group_id)
        members = [dict(item) for item in raw_members if isinstance(item, dict)] if isinstance(raw_members, list) else []
        cache[group_id] = {"ts": now, "items": members}
        return members

    def _format_atrelay_member(self, raw: dict[str, Any]) -> dict[str, Any]:
        user_id = str(raw.get("user_id") or "").strip()
        nickname = _single_line(raw.get("nickname"), 60)
        card = _single_line(raw.get("card"), 60)
        role = _single_line(raw.get("role"), 20) or "member"
        role_map = {"owner": "群主", "admin": "管理员", "member": "成员"}
        profile = self._worldbook_profile_by_user_id(user_id)
        if isinstance(profile, dict):
            if nickname:
                self._remember_worldbook_observed_name(user_id, nickname)
            if card:
                self._remember_worldbook_observed_name(user_id, card)
        return {
            "user_id": user_id,
            "nickname": nickname,
            "group_card": card or "",
            "role": role_map.get(role, role or "成员"),
            "relation_name": _single_line(profile.get("name"), 60) if isinstance(profile, dict) else "",
            "identity_note": _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 160) if isinstance(profile, dict) else "",
            "in_relation_net": bool(profile),
        }

    async def _resolve_atrelay_target_user(self, event: AstrMessageEvent, group_id: str, keyword: str) -> dict[str, Any]:
        query = _single_line(keyword, 128)
        if not query:
            return {}
        if query.isdigit():
            profile = self._worldbook_profile_by_user_id(query)
            return {
                "user_id": query,
                "name": _single_line(profile.get("name"), 60) if isinstance(profile, dict) else query,
                "source": "qq",
            }
        direct_id = self._normalize_atrelay_private_target_id(query)
        if direct_id:
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(direct_id) if isinstance(users, dict) else None
            return {
                "user_id": direct_id,
                "name": _single_line(user.get("nickname"), 60) if isinstance(user, dict) and user.get("nickname") else direct_id,
                "source": "private_user_id",
            }
        wb_matches = self._resolve_worldbook_member_by_name(query)
        if wb_matches:
            return wb_matches[0] if len(wb_matches) == 1 else {"ambiguous": True, "matches": wb_matches[:8], "source": "worldbook"}
        if self.atrelay_require_worldbook_first and runtime_persona_setting(
            self, "enable_worldbook_member_recognition", True
        ):
            return {}
        members = await self._get_group_member_list_for_tool(event, group_id)
        formatted = [self._format_atrelay_member(item) for item in members]
        hits = [
            item for item in formatted
            if query in item.get("user_id", "")
            or query in item.get("nickname", "")
            or query in item.get("group_card", "")
            or query in item.get("relation_name", "")
        ]
        if not hits:
            return {}
        if len(hits) > 1:
            return {"ambiguous": True, "matches": hits[:8], "source": "group_member_list"}
        hit = hits[0]
        return {"user_id": hit.get("user_id", ""), "name": hit.get("relation_name") or hit.get("group_card") or hit.get("nickname") or hit.get("user_id"), "source": "group_member_list"}

    def _atrelay_active_group_candidates_for_user(self, user_id: str, *, exclude_group_id: str = "") -> list[dict[str, Any]]:
        uid = _single_line(user_id, 40)
        if not uid:
            return []
        excluded = _single_line(exclude_group_id, 40)
        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        group_profiles = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        candidates: list[dict[str, Any]] = []
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            gid = _single_line(group.get("group_id") or group_id, 40)
            if not gid:
                continue
            if excluded and gid == excluded:
                continue
            members = group.get("members") if isinstance(group.get("members"), dict) else {}
            member = members.get(uid) if isinstance(members, dict) else None
            if not isinstance(member, dict):
                continue
            last_seen = _safe_float(member.get("last_seen"), 0)
            count = _safe_int(member.get("count"), 0, 0)
            if last_seen <= 0 and count <= 0:
                continue
            profile = group_profiles.get(gid) if isinstance(group_profiles, dict) else None
            label = (
                _single_line(group.get("name") or group.get("group_name") or group.get("display_name"), 80)
                or (_single_line(profile.get("name"), 80) if isinstance(profile, dict) else "")
                or gid
            )
            candidates.append(
                {
                    "group_id": gid,
                    "group_name": label,
                    "source": "recipient_active_group",
                    "last_seen": last_seen,
                    "count": count,
                    "score": last_seen + min(count, 5000) / 1000,
                }
            )
        candidates.sort(key=lambda item: (_safe_float(item.get("score"), 0), _safe_float(item.get("last_seen"), 0)), reverse=True)
        return candidates

    async def _resolve_atrelay_active_group_for_recipient(
        self,
        event: AstrMessageEvent,
        recipient_hint: str,
        *,
        exclude_current_group: bool = False,
    ) -> dict[str, Any]:
        recipient = _single_line(recipient_hint, 128)
        if not recipient:
            return {}
        resolved = await self._resolve_atrelay_target_user(event, "", recipient)
        if resolved.get("ambiguous"):
            return {"status": "ambiguous", "message": "收话人匹配到多个对象，请补充用户 ID", "matches": resolved.get("matches", [])[:8]}
        user_id = _single_line(resolved.get("user_id"), 128)
        if not user_id:
            return {}
        excluded = self._extract_group_id_from_event(event) if exclude_current_group else ""
        candidates = self._atrelay_active_group_candidates_for_user(user_id, exclude_group_id=excluded)
        if not candidates:
            return {}
        chosen = candidates[0]
        logger.info(
            "跨群转述按收话人活跃群自动选群: recipient=%s user=%s group=%s candidates=%s",
            recipient,
            user_id,
            chosen.get("group_id"),
            len(candidates),
        )
        return {"status": "success", **chosen, "recipient_user_id": user_id, "recipient_name": _single_line(resolved.get("name"), 60)}

    def _atrelay_tool_prompt_section(self) -> PromptSection | None:
        if not (self.enabled and self.enable_atrelay_tools):
            return None
        body = """当用户明确要求你“发到某个群”“告诉某个群友”“替我/帮我跟某人说一声”“帮我 @ 某人”“私聊某人”时,优先调用 `pc_relay_message`,不要在普通回复里预览要发送的完整内容。
- 统一入口：常见转述只用 `pc_relay_message`。你只需要整理 destination/group_hint/recipient_hint/message/relay_mode。
- 本轮主要目标是转述时,第一次 assistant 动作应直接调用工具,不要先输出普通文字；调用工具前不要发“我找找/我试试/找到了/正在查群号”之类的过程回复。
- 工具返回后只按结果简短回执,不要补关系评价、猜测对方反应或复述已发送内容。
- destination 规则：发群填 `group`,私聊填 `private`,不确定填 `auto`。私聊转群、群聊转私聊、群聊转群聊都走同一个工具。
- 群聊转私聊：只发送用户明确要求转述的内容,不要附带群聊上下文、内部记忆或“群里大家说了什么”。
- 发到群并点名某人：destination=`group`,group_hint=群号/群名,recipient_hint=目标昵称或 QQ,message=要说的话；工具会自动 @ 并解析关系网/群名片。
- 私聊某人：destination=`private`,recipient_hint=QQ 或关系网称呼；如果关系网无法唯一确认，再提供 group_hint 让工具从群成员里解析。
- 私聊询问并需要回报：用户说“帮我去私聊问问 A……”“问完告诉我”“他回了告诉我”时,调用 `pc_relay_message`，destination=`private`，并设置 `need_receipt=true`。对方下一次私聊回复后,工具会自动把回复带回当前发起会话。
- 回执二次确认：如果用户明确要求“问问能不能转回来/得到对方同意再告诉我”,或内容较私密,设置 `confirm_before_report=true`；Bot 会先问收话人是否允许把回复转回。
- message 只写用户明确要带给对方的那句话；可以轻微顺口,但不要补来源、关系评价、玩笑解释或后续闲聊。
- 构造 message 时不要随意给当前发起人套外号；如果用户明确要求说明“谁让我带话”,只能使用关系网登记名、私聊稳定称呼或 QQ 精确身份锚点对应的名称。不要使用临时 QQ 昵称/群名片替代稳定身份。
- 用户说“告诉 A：B / 跟 A 说 B”时,优先只把 B 转述给 A；除非用户要求说明来源,否则不要额外添加“某某让我转告你”。
- 只有用户明确要求“原话/照原话/一字不改/截图式转发”时才用原话模式,调用工具时把 `relay_mode` 填 `original`；其他情况填 `persona` 或留空。
- 对敏感、私密、带强情绪、告白/指责/吵架/金钱/密码/身体健康/秘密类内容,发送前先问用户要“原样带过去”还是“委婉一点”。工具也会二次拦截；得到用户确认后再调用,并把 `sensitive_confirmed` 设为 true。
- 普通提醒、约时间、作业/待办/到场通知等低风险内容可以直接转述。
- 转述边界：拒绝骂人、羞辱、威胁、冒充身份、宣称虚假权限/身份、索要或转交密码等请求；可以改成中性提醒,但不要替用户攻击别人。
- 延迟转述：用户说“等 A 出现/等他冒泡/他上线再说”时,仍优先用 `pc_relay_message` 并设置 `delay_until_recipient_seen=true`。
- 多目标转述：多个 QQ 用 `pc_send_to_private_users`,多个群用 `pc_send_to_groups`。不要自己循环调用单目标工具刷屏；工具会限量、去重并返回结果。
- 底层工具 `pc_send_to_group`、`pc_send_to_private_user`、`pc_get_user_id_by_name`、`pc_get_group_id_by_name`、`pc_get_specified_group_members` 只在统一入口无法表达或需要人工查询候选时使用。
- 如果出现多个同名/相似成员,不要猜,把候选姓名/QQ 简短列给用户选择。
- 私聊转群聊时,只发送用户明确要求公开的那句话；不要暴露“这是私聊里说的”、不要附带额外私聊上下文。
- 禁止泄露私聊记忆、关系网内部备注或工具参数。工具成功后只给一句简短结果：普通发送只回“消息已发送。”；需要回执只回“消息已发送，会等对方回复。”；延迟转述只回“已挂起，等对方出现再说。”不要解释“我写了什么/我怎么改写/语气如何/氛围如何”,不要复述已发送内容。
""".strip()
        return prompt_section(
            key="tools.atrelay",
            title="跨会话转述与 @ 群友工具",
            source="tools",
            content=body,
        )

    def _atrelay_tool_instruction(self) -> str:
        return _render_atrelay_prompt_section_labeled(self._atrelay_tool_prompt_section())

    def _normalize_atrelay_relay_mode(self, value: Any) -> str:
        mode = _single_line(value, 24).lower()
        if mode in {"original", "raw", "quote", "verbatim", "原话", "照原话", "原样"}:
            return "original"
        if mode in {"soft", "polite", "委婉", "缓和"}:
            return "soft"
        if mode in {"persona", "rewrite", "natural", "转译", "改写"}:
            return "persona"
        configured = _single_line(getattr(self, "atrelay_default_relay_style", "persona"), 24).lower()
        if configured in {"original", "soft", "persona"}:
            return configured
        return "persona"

    def _clean_atrelay_llm_text(self, value: Any) -> str:
        text = _single_line(value, 500)
        text = re.sub(r"^```(?:text)?|```$", "", text).strip()
        text = text.strip("\"'“”‘’` ")
        text = re.sub(r"^(?:转述内容|发送内容|正文|回复)[:：]\s*", "", text).strip()
        return _single_line(text, 500)

    def _atrelay_message_should_skip_llm_rewrite(self, text: str, relay_mode: str) -> bool:
        if self._normalize_atrelay_relay_mode(relay_mode) == "original":
            return True
        cleaned = _single_line(text, 80)
        if not cleaned:
            return True
        if len(cleaned) <= 8 and not re.search(r"[，,。！？!?；;：:\s]", cleaned):
            return True
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9ぁ-んァ-ンー]{1,12}[~～!！。]?", cleaned):
            short_tokens = (
                "贴贴", "摸摸", "抱抱", "晚安", "早安", "午安", "辛苦了", "加油",
                "笨蛋", "在吗", "收到", "谢谢", "对不起", "喜欢你",
            )
            if any(token in cleaned for token in short_tokens):
                return True
        return False

    def _atrelay_rewrite_looks_polluted(
        self,
        rewritten: str,
        *,
        original: str,
        source_name: str,
        recipient_name: str,
    ) -> bool:
        text = _single_line(rewritten, 500)
        if not text:
            return True
        if len(text) > max(80, len(original) * 3):
            return True
        source = _single_line(source_name, 40)
        if source and source in text and source not in original:
            return True
        noisy_tokens = (
            "让我", "叫我", "托我", "带话", "转告", "他说", "她说", "TA说",
            "刚才", "估计", "应该挺", "你们关系", "关系真好", "算账",
            "结果是", "哈哈", "嘿嘿", "我顺手", "我帮", "我来",
        )
        if any(token in text for token in noisy_tokens if token not in original):
            return True
        recipient = _single_line(recipient_name, 40)
        if recipient and text.startswith(recipient) and not original.startswith(recipient):
            return True
        return False

    def _atrelay_identity_label(self, user_id: str, fallback: str = "") -> str:
        uid = _single_line(user_id, 40)
        name = ""
        if uid:
            try:
                profile = self._worldbook_profile_by_user_id(uid)
            except Exception:
                profile = None
            if isinstance(profile, dict):
                name = _single_line(profile.get("name"), 40)
            users = self.data.get("users") if isinstance(self.data.get("users"), dict) else {}
            user = users.get(uid) if isinstance(users, dict) else None
            if isinstance(user, dict):
                name = name or _single_line(user.get("stable_name") or user.get("nickname"), 40)
            if not name:
                if isinstance(user, dict):
                    name = _single_line(user.get("display_name") or user.get("last_display_name"), 40)
        name = name or _single_line(fallback, 40) or uid or "对方"
        return f"{name}（ID:{uid}）" if uid else name

    def _atrelay_source_identity_label(self, event: AstrMessageEvent) -> str:
        user_id = self._atrelay_event_user_id(event)
        fallback = ""
        try:
            fallback = self._sender_display_name(event)
        except Exception:
            fallback = ""
        return self._atrelay_identity_label(user_id, fallback)

    @staticmethod
    def _atrelay_rewrite_prompt_section(
        *,
        destination: str,
        source_label: str,
        recipient_label: str,
        mode: str,
        original: str,
    ) -> PromptSection:
        return prompt_section(
            key="background.atrelay_rewrite",
            title="转述正文改写",
            source="atrelay",
            template=(
                "把下面这句稍微顺成 Bot 准备发给收话人的一句话。\n"
                "只输出要发送的正文，不要解释，不要加引号，不要说已经发送。\n"
                "只保留用户要转述给收话人的内容；不要加入未给出的事实、私聊上下文、群聊上下文、系统信息或关系网备注。\n"
                "不要添加称呼、来源、关系评价、玩笑解释、后续评论或“我帮忙带话”的说明。\n"
                "发起人和收话人是两个不同角色；不要把发起人的名字写进正文,不要把收话人的名字当成正在和 Bot 说话的人。\n"
                "如果原句已经很短或很自然,直接原样输出。\n"
                "发送场景：{destination}\n"
                "发起人：{source_label}\n"
                "收话人：{recipient_label}\n"
                "风格：{style}\n"
                "原句：{original}\n"
                "正文："
            ),
            variables={
                "destination": "群聊点名" if destination == "group" else "私聊",
                "source_label": source_label,
                "recipient_label": recipient_label,
                "style": "稍微委婉" if mode == "soft" else "自然转述",
                "original": original,
            },
        )

    async def _rewrite_atrelay_message_with_llm(
        self,
        event: AstrMessageEvent,
        *,
        destination: str,
        recipient_hint: str,
        text: str,
        relay_mode: str,
    ) -> str:
        original = self._normalize_atrelay_text(text, limit=800)
        if not original:
            return original
        if not bool(getattr(self, "enable_atrelay_llm_rewrite", True)):
            return original
        mode = self._normalize_atrelay_relay_mode(relay_mode)
        if self._atrelay_message_should_skip_llm_rewrite(original, mode):
            return original
        recipient = _single_line(recipient_hint, 80) or "对方"
        source_label = self._atrelay_source_identity_label(event)
        recipient_label = self._atrelay_identity_label(recipient, recipient)
        prompt = render_prompt_sections(
            [
                self._atrelay_rewrite_prompt_section(
                    destination=destination,
                    source_label=source_label,
                    recipient_label=recipient_label,
                    mode=mode,
                    original=original,
                )
            ],
            mode=PromptRenderMode.BODY_ONLY,
        )
        try:
            rewritten = await self._llm_call(
                prompt,
                max_tokens=120,
                provider_id=self._task_provider(
                    runtime_persona_setting(
                        self,
                        "MAI_STYLE_PROVIDER_ID",
                        getattr(self, "mai_style_provider_id", ""),
                    ),
                    runtime_persona_setting(
                        self,
                        "LLM_PROVIDER_ID",
                        getattr(self, "llm_provider_id", ""),
                    ),
                ),
                task="atrelay_rewrite",
            )
        except Exception as exc:
            logger.info("转述正文 LLM 转译失败: %s", _single_line(exc, 120))
            return original
        cleaned = self._clean_atrelay_llm_text(rewritten)
        if not cleaned:
            return original
        cleaned = self._normalize_atrelay_text(cleaned, limit=800)
        recipient_name = _single_line(recipient_label.split("（", 1)[0], 40)
        source_name = _single_line(source_label.split("（", 1)[0], 40)
        for name in (source_name, recipient_name):
            if name and not original.startswith(name):
                cleaned = re.sub(rf"^{re.escape(name)}[，,：:\s]+", "", cleaned).strip()
        if self._atrelay_rewrite_looks_polluted(
            cleaned,
            original=original,
            source_name=source_name,
            recipient_name=recipient_name,
        ):
            logger.info(
                "转述正文 LLM 转译疑似扩写污染,使用原文: before=%s after=%s",
                _single_line(original, 80),
                _single_line(cleaned, 120),
            )
            return original
        logger.info("转述正文已 LLM 转译: before=%s after=%s", _single_line(original, 80), _single_line(cleaned, 120))
        return cleaned

    @staticmethod
    def _atrelay_text_is_delivery_receipt(text: str) -> bool:
        """Match a whole delivery receipt, never a receipt embedded in a reply."""
        return re.fullmatch(
            r"\s*(?:(?:消息)?(?:已(?:经)?(?:成功)?发送(?:成功|完成|完毕)?|"
            r"发送(?:成功|完成|完毕))|已(?:经)?(?:发送|发)给(?:用户|对方)|"
            r"message\s*sent(?:\s+to\s+session\s+[^\s。！!?]+)?|sent)[。.!！?？\s]*",
            text,
            flags=re.IGNORECASE,
        ) is not None

    async def _atrelay_final_receipt_text(self, event: AstrMessageEvent, result: dict[str, Any]) -> str:
        final_reply = _single_line(result.get("final_reply"), 80) or "说过啦。"
        reference = _single_line(result.get("final_reply_reference"), 260)
        if reference:
            sender_id = self._atrelay_event_user_id(event)
            users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
            user = users.get(sender_id) if sender_id and isinstance(users, dict) and isinstance(users.get(sender_id), dict) else {}
            rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
            if callable(rewriter):
                rewritten = await rewriter(
                    reference,
                    scene="转述工具执行后的聊天回执",
                    user=user,
                    event=event,
                    fallback_text=final_reply,
                    task="atrelay_receipt_rewrite",
                    max_chars=70,
                    allow_fallback=True,
                    preserve_status=True,
                )
                if rewritten:
                    final_reply = rewritten
        return final_reply

    @filter.on_llm_response()
    async def compact_atrelay_tool_final_response(self, event: AstrMessageEvent, resp: LLMResponse, *args, **kwargs):
        """仅把转述工具的完整投递回执改成自然短句，保留正常回复。"""
        if self is None or not self.enabled or resp is None:
            return
        result = getattr(event, "private_companion_atrelay_tool_result", None)
        if not isinstance(result, dict) or _single_line(result.get("status"), 24) not in {"success", "scheduled"}:
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if text and not self._atrelay_text_is_delivery_receipt(text):
            return
        resp.completion_text = await self._atrelay_final_receipt_text(event, result)

    async def _rewrite_atrelay_delivery_receipt_before_send(self, event: AstrMessageEvent) -> None:
        result = event.get_result()
        if result is None or not result.chain or any(not isinstance(comp, Plain) for comp in result.chain):
            return
        text = "\n".join(comp.text for comp in result.chain).strip()
        if not self._atrelay_text_is_delivery_receipt(text):
            return
        tool_result = getattr(event, "private_companion_atrelay_tool_result", None)
        if not isinstance(tool_result, dict) or _single_line(tool_result.get("status"), 24) not in {"success", "scheduled"}:
            return
        final_reply = await self._atrelay_final_receipt_text(event, tool_result)
        result.chain = [Plain(final_reply)]
