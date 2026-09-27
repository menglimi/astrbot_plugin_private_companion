# -*- coding: utf-8 -*-
"""UserMemoryContextPromptPart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_context_prompt.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 457 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryContextPromptMixin）。
"""
from __future__ import annotations
from .user_memory_context_prompt_shared import Any
from .user_memory_context_prompt_shared import PromptSection
from .user_memory_context_prompt_shared import _normalize_photo_subject_owner
from .user_memory_context_prompt_shared import _now_ts
from .user_memory_context_prompt_shared import _photo_subject_owner_prompt_label
from .user_memory_context_prompt_shared import _render_conversation_section_labeled
from .user_memory_context_prompt_shared import _safe_float
from .user_memory_context_prompt_shared import _safe_int
from .user_memory_context_prompt_shared import _single_line
from .user_memory_context_prompt_shared import _strip_internal_message_blocks
from .user_memory_context_prompt_shared import datetime
from .user_memory_context_prompt_shared import hashlib
from .user_memory_context_prompt_shared import prompt_section
from .user_memory_context_prompt_shared import random
from .user_memory_context_prompt_shared import re
from .user_memory_context_prompt_shared import runtime_persona_setting



class UserMemoryContextPromptPart01Mixin:
    """UserMemoryContextPromptPart01Mixin（从 UserMemoryContextPromptMixin 拆出）。"""


    def _format_emotion_inertia_prompt_section(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> PromptSection | None:
        """Turn recent Bot-targeted emotion events into a decaying voice residue."""
        if not isinstance(user, dict):
            return None
        check_now = _now_ts() if now is None else now
        ledger = user.get("emotion_event_ledger")
        if not isinstance(ledger, list):
            return None
        signs = {
            "hurt": -1,
            "boundary_violation": -1,
            "boundary": -1,
            "scar_touched": -1,
            "apology": 1,
            "comfort": 1,
            "praise": 1,
            "play": 1,
            "intimacy": 1,
            "warm_memory": 1,
            "vulnerable_resonance": 1,
        }
        weighted = 0.0
        newest_at = 0.0
        newest_type = ""
        for item in ledger[-32:]:
            if not isinstance(item, dict):
                continue
            event_type = _single_line(item.get("event_type"), 48).lower()
            sign = signs.get(event_type)
            if sign is None or _single_line(item.get("status"), 24) in {"ignored", "expired"}:
                continue
            target = item.get("target_ref") if isinstance(item.get("target_ref"), dict) else {}
            target_kind = _single_line(target.get("kind"), 24).lower()
            target_role = _single_line(target.get("role"), 40).lower()
            if target_kind not in {"bot", "self"} and target_role not in {"bot", "bot_self"}:
                continue
            occurred = _single_line(item.get("occurred_at"), 48)
            try:
                occurred_at = datetime.fromisoformat(occurred.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                continue
            age = check_now - occurred_at
            if age < 0 or age > 30 * 3600:
                continue
            intensity = _safe_float(item.get("intensity"), 0)
            confidence = _safe_float(item.get("confidence"), 0)
            decay = 0.5 ** (age / (8 * 3600))
            weighted += sign * intensity * confidence * decay
            if occurred_at > newest_at:
                newest_at = occurred_at
                newest_type = event_type
        if abs(weighted) < 6.0:
            return None
        if weighted < 0:
            residue = "仍有一点受伤、疲惫或收敛的余温"
            direction = "即使当前出现开心内容，也只逐步回暖，不要瞬间跳成过度兴奋或亲昵"
        else:
            residue = "仍有一点被安慰、被肯定或亲近后的暖意"
            direction = "暖意可以留在语气里，但不能覆盖当前边界、任务或用户的真实情绪"
        body = "\n".join([
                f"近期互动留下的衰减余温：{residue}（最近事件={newest_type}）。",
                f"{direction}；单个新事件最多让外显情绪移动一档，跨档需要时间或多次真实事件累积。",
                "这是语气约束，不是必须说出口的台词；不要提情绪账本、档位、分数或内部事件。",
        ])
        return prompt_section(
            key="state.emotion_inertia",
            title="情绪惯性",
            source="emotion_ledger",
            content=body,
        )

    def _format_emotion_inertia_prompt(
        self,
        user: dict[str, Any],
        *,
        now: float | None = None,
    ) -> str:
        return _render_conversation_section_labeled(
            self._format_emotion_inertia_prompt_section(user, now=now)
        )

    def _format_private_reunion_prompt_section(
        self,
        user: dict[str, Any],
        inbound_text: str,
        *,
        now: float | None = None,
    ) -> PromptSection | None:
        if not isinstance(user, dict):
            return None
        check_now = _now_ts() if now is None else now
        observed_at = _safe_float(user.get("last_inbound_gap_observed_at"), 0)
        gap = _safe_float(user.get("last_inbound_gap_seconds"), 0)
        if observed_at <= 0 or check_now - observed_at > 10 * 60 or gap < 3 * 24 * 3600:
            return None
        if _safe_float(user.get("last_reunion_ack_at"), 0) >= observed_at:
            return None
        days = max(3, int(gap // (24 * 3600)))
        intensity = "明显的久别重逢感" if days >= 7 else "轻微的久别感"
        departure = user.get("conversation_departure") if isinstance(user.get("conversation_departure"), dict) else {}
        departure_at = _safe_float(departure.get("at"), 0)
        previous_user_at = observed_at - gap
        departed = previous_user_at <= departure_at <= observed_at
        task_like = bool(
            re.search(r"[？?]|(?:帮我|怎么|为什么|能否|请|排查|修复|写一份|告诉我)", inbound_text)
        )
        body = "\n".join([
                f"用户距离上次主动来聊约 {days} 天，本轮是回来后的第一条消息，应该有{intensity}。",
                "可以用一个很短的惊喜、想念或‘好久不见’式承接，但不得控诉、查岗、算账或要求解释这几天去了哪里。",
                "如果期间 Bot 发过主动消息，不得声称双方完全没有联系；只表达用户重新出现带来的感受。",
                "上次由 Bot 自己自然收尾，本次按重新接上线处理。" if departed else "",
                "当前消息带有明确问题或任务，久别感最多占一句，随后立即回答正事。" if task_like else "不要为了表现时间差而编造这几天发生的事。",
        ]).strip()
        return prompt_section(
            key="conversation.reunion",
            title="久别重逢的时间感",
            source="conversation",
            content=body,
        )

    def _format_private_reunion_prompt(
        self,
        user: dict[str, Any],
        inbound_text: str,
        *,
        now: float | None = None,
    ) -> str:
        return _render_conversation_section_labeled(
            self._format_private_reunion_prompt_section(
                user,
                inbound_text,
                now=now,
            )
        )

    def _format_conversation_departure_prompt_section(
        self,
        user: dict[str, Any],
        inbound_text: str,
        state: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> PromptSection | None:
        if not isinstance(user, dict) or self._private_user_role(user) != "owner":
            return None
        check_now = _now_ts() if now is None else now
        if _safe_int(user.get("episode_message_count"), 0, 0) < 6:
            return None
        compact = _single_line(inbound_text, 80)
        if not compact or len(compact) > 18 or re.search(r"[？?]", compact):
            return None
        if re.search(
            r"(?:帮我|为什么|怎么|怎么办|能不能|可以吗|不舒服|难受|焦虑|害怕|生气|出事|救命|继续|然后呢)",
            compact,
        ):
            return None
        last_offer = _safe_float(user.get("conversation_departure_offer_at"), 0)
        if last_offer > 0 and check_now - last_offer < 3 * 24 * 3600:
            return None
        if random.random() > 0.14:
            return None
        user["conversation_departure_offer_at"] = check_now
        mood = _single_line((state or {}).get("mood_bias"), 24)
        energy = _safe_int((state or {}).get("energy"), 70, 0, 100)
        body = "\n".join([
                f"这段对话已经持续了一会儿，当前用户只发了低信息短句；Bot 当前情绪={mood or '平稳'}、精力约={energy}/100。",
                "这一轮允许像真人一样主动收尾：先自然接住短句，再简短说自己要去休息或处理手头的事，不再追加问题。也可以判断仍有话可聊而不退场。",
                "只能使用已有日程素材或笼统说‘先忙会儿/歇一会儿’，不得临时编造具体工作、地点、约会或现实承诺。",
                "语气要轻，不要暗示用户冷淡，不要让用户承担挽留义务，也不要解释这是策略或候选。",
        ])
        return prompt_section(
            key="conversation.departure",
            title="自然退场候选",
            source="conversation",
            content=body,
        )

    def _format_conversation_departure_prompt(
        self,
        user: dict[str, Any],
        inbound_text: str,
        state: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> str:
        return _render_conversation_section_labeled(
            self._format_conversation_departure_prompt_section(
                user,
                inbound_text,
                state,
                now=now,
            )
        )

    @staticmethod
    def _bot_preference_category(text: str) -> str:
        categories = (
            ("music", ("歌", "音乐", "歌手", "曲子", "专辑", "旋律", "听", "爵士")),
            ("food", ("吃", "喝", "味道", "甜", "辣", "咖啡", "茶", "饮料", "菜")),
            ("media", ("电影", "剧", "番", "动漫", "小说", "书", "漫画", "专栏")),
            ("game", ("游戏", "玩", "对局", "五子棋", "棋")),
            ("aesthetic", ("颜色", "穿", "衣服", "风格", "花", "香味", "天气", "季节")),
        )
        for category, tokens in categories:
            if any(token in text for token in tokens):
                return category
        return ""

    def _record_confirmed_bot_continuity(
        self,
        user: dict[str, Any],
        response_text: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Persist only confirmed Bot-side continuity signals from visible text."""
        if not isinstance(user, dict):
            return False
        check_now = _now_ts() if now is None else now
        text = _single_line(_strip_internal_message_blocks(response_text, enabled=bool(runtime_persona_setting(self, "enable_framework_error_leak_guard", True))), 1200)
        if not text:
            return False
        changed = False
        preferences = user.get("bot_self_preferences")
        if not isinstance(preferences, list):
            preferences = []
        clauses = [part.strip() for part in re.split(r"[。！？!?\n]+", text) if part.strip()]
        for clause in clauses[:16]:
            match = re.search(
                r"(?:^|[，,])((?:我|本小姐|咱)(?:(?:一直|其实|还是|最|更|挺|很|不太|不怎么)){0,3}"
                r"(?:喜欢|偏爱|爱吃|爱喝|爱听|常听|不喜欢|不爱吃|不爱喝|讨厌)[^，,；;]{1,56})",
                clause,
            )
            statement = _single_line(match.group(1), 100) if match else ""
            category = self._bot_preference_category(statement)
            if not statement or not category or re.search(r"(?:如果|假如|也许|可能|大概|你喜欢|喜欢你)", statement):
                continue
            fingerprint = hashlib.sha1(statement.encode("utf-8")).hexdigest()[:20]
            preferences = [
                item
                for item in preferences
                if isinstance(item, dict)
                and _single_line(item.get("fingerprint"), 40) != fingerprint
            ]
            preferences.append(
                {
                    "fingerprint": fingerprint,
                    "category": category,
                    "statement": statement,
                    "at": check_now,
                    "source": "confirmed_visible_reply",
                }
            )
            changed = True
        if changed:
            user["bot_self_preferences"] = preferences[-24:]

        offered_at = _safe_float(user.get("conversation_departure_offer_at"), 0)
        if offered_at > 0 and 0 <= check_now - offered_at <= 3 * 3600 and re.search(
            r"(?:我先(?:去|睡|休息|忙|写|看|处理|收拾|洗漱)|我去.{0,16}了|先不聊|晚点再聊|回头再聊|我先撤)",
            text,
        ):
            departure = {
                "at": check_now,
                "text": _single_line(text, 180),
                "kind": "bot_initiated_close",
            }
            user["conversation_departure"] = departure
            continuity = user.setdefault("state_continuity", {})
            if not isinstance(continuity, dict):
                continuity = {}
                user["state_continuity"] = continuity
            continuity["conversation_departure"] = departure
            user["episode_message_count"] = 0
            user["awaiting_reply_since"] = 0
            changed = True
        return changed

    def _format_bot_self_preference_consistency_prompt_section(
        self,
        user: dict[str, Any],
        inbound_text: str,
    ) -> PromptSection | None:
        if not isinstance(user, dict):
            return None
        preferences = user.get("bot_self_preferences")
        if not isinstance(preferences, list):
            return None
        inbound = _single_line(inbound_text, 220)
        requested_categories = {
            category
            for category in ("music", "food", "media", "game", "aesthetic")
            if self._bot_preference_category(inbound) == category
        }
        generic_query = bool(re.search(r"你(?:自己)?(?:喜欢|偏爱|爱吃|爱喝|爱听|讨厌|不喜欢)(?:什么|哪|啥)", inbound))
        selected: list[dict[str, Any]] = []
        for item in reversed(preferences):
            if not isinstance(item, dict):
                continue
            category = _single_line(item.get("category"), 24)
            statement = _single_line(item.get("statement"), 100)
            if not statement or (not generic_query and category not in requested_categories):
                continue
            if category in {_single_line(existing.get("category"), 24) for existing in selected}:
                continue
            selected.append(item)
            if len(selected) >= 4:
                break
        if not selected:
            return None
        statements = "\n".join(f"- {_single_line(item.get('statement'), 100)}" for item in selected)
        body = "\n".join([
                "下面是 Bot 过去实际发送过的自身偏好表达，不是用户偏好：",
                statements,
                "相关话题下不得无缘无故说出相反偏好；不必机械复述。若确实要改变，可以自然表达‘最近口味变了’，但不能假装从未说过。",
        ])
        return prompt_section(
            key="persona.preference_continuity",
            title="Bot 自身偏好连续性",
            source="bot_self_history",
            content=body,
        )

    def _format_bot_self_preference_consistency(
        self,
        user: dict[str, Any],
        inbound_text: str,
    ) -> str:
        return _render_conversation_section_labeled(
            self._format_bot_self_preference_consistency_prompt_section(
                user,
                inbound_text,
            )
        )

    @staticmethod
    def _inbound_explicitly_owns_recent_media_event(inbound_text: str) -> bool:
        """Return whether the user explicitly says the depicted event happened to/by them."""
        inbound = _single_line(inbound_text, 220)
        if not inbound:
            return False
        # Common exclamations and observation phrases contain “我” without assigning
        # the depicted action to the user (for example “我的天，洒出来了”).
        if re.match(r"^(?:我的天|我天|我去|我靠|我艹|我草|我勒个|我看(?:见|到|着)?|我觉得|我感觉|我想说)", inbound):
            return False
        return bool(
            re.search(
                r"(?:^|[，,。！？!?\s])(?:是)?我(?:自己)?"
                r"[^。！？!?\n]{0,12}"
                r"(?:把|将|弄|搞|打翻|碰倒|弄倒|洒|撒|溅|摔|掉|弄坏|打碎|"
                r"受伤|烫|割|磕|撞|做的|干的|画的|拍的|发的)",
                inbound,
            )
        )

    def _recent_proactive_media_ownership_context(
        self,
        user: dict[str, Any],
        inbound_text: str = "",
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Resolve a short reply as commentary on the Bot's latest proactive image."""
        if not isinstance(user, dict):
            return {}
        inbound = _single_line(inbound_text, 220)
        if not inbound or self._inbound_explicitly_owns_recent_media_event(inbound):
            return {}

        check_now = _now_ts() if now is None else now
        action = _single_line(user.get("last_proactive_action"), 80).lower()
        action_parts = {part.strip() for part in action.split("+") if part.strip()}
        action_is_photo = "photo_text" in action_parts or "photo_text" in action
        last_proactive_at = _safe_float(user.get("last_proactive_sent_at"), 0)

        snapshot = user.get("last_photo_share_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        snapshot_at = _safe_float(snapshot.get("sent_at"), 0)
        snapshot_expires_at = _safe_float(snapshot.get("expires_at"), 0) or snapshot_at + 12 * 3600
        snapshot_is_live = snapshot_at > 0 and check_now < snapshot_expires_at
        newer_non_photo_proactive = (
            last_proactive_at > snapshot_at + 1
            and not action_is_photo
        )
        if not action_is_photo and (not snapshot_is_live or newer_non_photo_proactive):
            return {}

        sent_at = max(last_proactive_at if action_is_photo else 0, snapshot_at if snapshot_is_live else 0)
        age = check_now - sent_at
        if sent_at <= 0 or age < 0:
            return {}

        compact = self._compact_repeat_text(inbound)
        direct_media_reference = bool(
            re.search(r"(?:这|那|刚才|你发的)?(?:张)?(?:图|图片|照片|画面|里面|图里|照片里)", inbound)
        )
        reaction_cues = (
            "洒", "撒", "溅", "打翻", "翻了", "翻车", "摔", "掉", "倒了", "漏", "碎", "破",
            "糊", "焦", "坏", "着火", "冒烟", "脏", "湿", "好看", "漂亮", "可爱", "吓", "危险",
            "小心", "完了", "救命", "哈哈", "笑死", "啊", "怎么", "手", "疼",
        )
        short_reaction = len(compact) <= 48 and any(cue in inbound for cue in reaction_cues)
        if direct_media_reference:
            if age > 12 * 3600:
                return {}
        elif not short_reaction or age > 30 * 60:
            return {}

        caption = _single_line(snapshot.get("caption"), 260)
        if not caption:
            summary = _single_line(user.get("last_proactive_behavior_summary"), 300)
            caption = _single_line(re.split(r"[:：]", summary, maxsplit=1)[-1], 260) if summary else ""
        return {
            "sent_at": sent_at,
            "action": action or "photo_text",
            "caption": caption,
            "subject_owner": _normalize_photo_subject_owner(snapshot.get("subject_owner")) or "unknown",
            "proactive_text": _single_line(user.get("last_proactive_message"), 300),
        }

    def _format_recent_proactive_media_ownership_prompt_section(
        self,
        user: dict[str, Any],
        inbound_text: str = "",
    ) -> PromptSection | None:
        context = self._recent_proactive_media_ownership_context(user, inbound_text)
        if not context:
            return None
        caption = _single_line(context.get("caption"), 260)
        subject_owner = _normalize_photo_subject_owner(context.get("subject_owner")) or "unknown"
        owner_label = _photo_subject_owner_prompt_label(subject_owner)
        if subject_owner == "bot":
            ownership_rule = "- 结构化主体归属为 Bot：图中由“我/她/角色本人”做出的动作属于 Bot/当前人格。"
        else:
            ownership_rule = f"- 结构化主体归属为{owner_label}；动作属于该画面主体，不属于用户，也不要擅自改判成 Bot。"
        body = "\n".join(
            part
            for part in (
                "- 用户是在评价 Bot 刚才主动发出的图片，不是在报告自己做了图中的事。",
                f"- 图片发送者：Bot/当前人格；画面主体：{owner_label}",
                f"- 刚才图片画面：{caption}" if caption else "",
                ownership_rule,
                "- 除非用户明确说“我把……弄洒了/做了”，否则绝不能把图中动作安到用户身上。",
                "- 回复应从 Bot 或真实画面主体的角度承接，可以自然承认、自嘲或回应用户的担心；不得责怪用户笨手笨脚，也不得询问用户有没有被图中事件弄伤、弄湿或溅到。",
            )
            if part
        )
        return prompt_section(
            key="media.proactive_ownership",
            title="本轮主动图片归属（高优先级）",
            source="user_memory",
            content=body,
        )

    def _format_recent_proactive_media_ownership_guard(
        self,
        user: dict[str, Any],
        inbound_text: str = "",
    ) -> str:
        return _render_conversation_section_labeled(
            self._format_recent_proactive_media_ownership_prompt_section(
                user,
                inbound_text,
            )
        )
