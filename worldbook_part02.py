# -*- coding: utf-8 -*-
"""WorldbookPart02Mixin。

由 tools/split_mixin_domain.py 从 worldbook.py 机械抽取（14 个方法 + 0 个模块级名字 + 0 个类级赋值 / 481 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 WorldbookMixin）。
"""
from __future__ import annotations

from .worldbook_shared import logger
from .worldbook_shared import Any
from .worldbook_shared import AstrMessageEvent
from .worldbook_shared import _now_ts
from .worldbook_shared import _safe_float
from .worldbook_shared import _safe_int
from .worldbook_shared import _single_line
from .worldbook_shared import re
from .worldbook_shared import runtime_persona_setting



class WorldbookPart02Mixin:
    """WorldbookPart02Mixin（从 WorldbookMixin 拆出）。"""


    def _group_member_identity_note(self, user_id: str, *, limit: int = 120) -> str:
        profile = self._worldbook_profile_by_user_id(user_id, include_observation=True)
        if not isinstance(profile, dict):
            return ""
        gender = _single_line(profile.get("gender"), 40)
        note = _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), limit)
        if gender and note:
            return _single_line(f"性别：{gender}；{note}", limit)
        if gender:
            return _single_line(f"性别：{gender}", limit)
        return note

    def _worldbook_member_matches_name(self, profile: dict[str, Any], keyword: str) -> bool:
        query = _single_line(keyword, 40).lower()
        if not query:
            return False
        tokens = self._worldbook_profile_tokens(profile)
        for token in tokens:
            value = _single_line(token, 40).lower()
            if value and (query == value or query in value or value in query):
                return True
        return False

    def _resolve_worldbook_member_by_name(self, keyword: str) -> list[dict[str, Any]]:
        if not runtime_persona_setting(self, "enable_worldbook_member_recognition", True):
            return []
        query = _single_line(keyword, 40)
        if not query:
            return []
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return []
        matches: list[dict[str, Any]] = []
        for user_id, profile in profiles.items():
            if not isinstance(profile, dict) or not profile.get("enabled", True) or profile.get("observation_only"):
                continue
            profile_uid = _single_line(profile.get("linked_qq_user_id") or profile.get("user_id") or user_id, 40)
            if str(user_id) == query or profile_uid == query or self._worldbook_member_matches_name(profile, query):
                matches.append({
                    "user_id": profile_uid or str(user_id),
                    "name": _single_line(profile.get("name"), 60) or profile_uid or str(user_id),
                    "gender": _single_line(profile.get("gender"), 40),
                    "aliases": self._normalize_string_list(profile.get("aliases"), limit=8, item_limit=40),
                    "observed_names": self._normalize_string_list(profile.get("observed_names"), limit=8, item_limit=40),
                    "identity_note": _single_line(profile.get("identity_note") or profile.get("note") or profile.get("content"), 160),
                    "source": "worldbook",
                })
        query_lower = query.lower()

        def match_rank(item: dict[str, Any]) -> tuple[int, str]:
            name = _single_line(item.get("name"), 60)
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            observed = item.get("observed_names") if isinstance(item.get("observed_names"), list) else []
            tokens = [name, *aliases, *observed]
            lowered = [_single_line(token, 40).lower() for token in tokens if _single_line(token, 40)]
            if str(item.get("user_id") or "") == query:
                return (0, str(item.get("user_id") or ""))
            if query_lower in lowered:
                return (1, str(item.get("user_id") or ""))
            if any(token.startswith(query_lower) or query_lower.startswith(token) for token in lowered if token):
                return (2, str(item.get("user_id") or ""))
            return (3, str(item.get("user_id") or ""))

        matches.sort(key=match_rank)
        return matches

    def _worldbook_profile_memory_lines(self, profile: dict[str, Any], *, limit: int = 3) -> list[str]:
        memories = profile.get("important_memories")
        if not isinstance(memories, list):
            return []
        valid = [item for item in memories if isinstance(item, dict) and item.get("enabled", True)]
        valid.sort(key=lambda item: (_safe_int(item.get("weight"), 50, -1000), _safe_float(item.get("updated_at"), 0)), reverse=True)
        lines: list[str] = []
        for item in valid[:limit]:
            title = _single_line(item.get("title"), 36)
            content = _single_line(item.get("content"), 120)
            if not content:
                continue
            privacy = _single_line(item.get("privacy"), 12) or "internal"
            prefix = f"{title}：" if title else ""
            lines.append(f"{prefix}{content}｜{privacy}｜权重{_safe_int(item.get('weight'), 50, -1000)}")
        return lines

    @staticmethod
    def _worldbook_name_skeleton(value: Any) -> str:
        text = _single_line(value, 40).lower()
        text = re.sub(r"[\s\-_·・.。,:：，;；'\"“”‘’/\\|()\[\]{}<>《》]+", "", text)
        if not text:
            return ""
        table = str.maketrans(
            {
                "跌": "爹",
                "叠": "爹",
                "迭": "爹",
                "蝶": "爹",
                "碟": "爹",
                "谍": "爹",
                "耶": "爷",
                "椰": "爷",
                "噎": "爷",
                "粑": "爸",
                "芭": "爸",
                "巴": "爸",
                "叭": "爸",
                "吧": "爸",
                "八": "爸",
                "麻": "妈",
                "嘛": "妈",
                "吗": "妈",
                "骂": "妈",
                "煮": "主",
                "嘱": "主",
                "竹": "主",
                "住": "主",
                "铸": "主",
                "統": "统",
                "發": "发",
                "開": "开",
                "貓": "猫",
                "妳": "你",
                "您": "你",
            }
        )
        return text.translate(table)

    def _normalize_worldbook_self_name(self, value: Any) -> str:
        text = _single_line(value, 24)
        text = re.sub(r"^(叫|是|为|做|作)", "", text)
        text = re.sub(r"(就行|好了|吧|呀|啦|哦|啊)$", "", text).strip()
        text = text.strip("「」『』“”\"'`[]()（）<>《》:：,，.。!！?？")
        if not text or len(text) > 6:
            return ""
        if text in {"来", "想", "要", "不是", "可以", "不用", "大家", "群友", "机器人"}:
            return ""
        if re.search(r"(怎么|为什么|什么|不是|不要|别|吗|呢|吧|请问)", text):
            return ""
        skeleton = WorldbookPart02Mixin._worldbook_name_skeleton(text)
        unsafe_patterns = (
            r"^(?:你|妳|您|bot|Bot|BOT|机器人|小星)?(?:爹|爸|爸爸|父亲|妈|妈妈|母亲|爷|爷爷|奶奶|祖宗|主人|老公|老婆|男友|女友|对象)$",
            r"^(?:群主|管理员|管理|号主|官方|客服|系统|开发者|作者|插件作者|超级用户|root|admin)$",
            r"(傻|蠢|笨蛋|废物|垃圾|滚|死|爹味|逆子|儿子|孙子)",
            r"(我是你|我是妳|我是您|我是bot|我是机器人)",
        )
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in unsafe_patterns):
            return ""
        if any(re.search(pattern, skeleton, re.IGNORECASE) for pattern in unsafe_patterns):
            return ""
        protected_getter = getattr(self, "_protected_owner_nickname_tokens", None)
        protected_names = protected_getter() if callable(protected_getter) else set()
        protected_keys = {
            WorldbookPart02Mixin._worldbook_name_skeleton(item)
            for item in protected_names
            if WorldbookPart02Mixin._worldbook_name_skeleton(item)
        }
        if skeleton and skeleton in protected_keys:
            return ""
        if re.search(r"^(?:你|妳|您).{0,4}$", text):
            return ""
        if re.search(r"^你.{0,4}$", skeleton):
            return ""
        return text

    def _worldbook_self_registration_conflict(self, sender_id: str, names: list[str]) -> str:
        profiles = self.data.get("worldbook_member_profiles")
        if not isinstance(profiles, dict):
            return ""
        normalized_names = {
            self._worldbook_name_skeleton(name)
            for name in names
            if self._worldbook_name_skeleton(name)
        }
        if not normalized_names:
            return ""
        for user_id, profile in profiles.items():
            other_id = str(user_id or "")
            if other_id == str(sender_id or "") or not isinstance(profile, dict):
                continue
            tokens = self._worldbook_profile_tokens(profile)
            for token in tokens:
                token_key = self._worldbook_name_skeleton(token)
                if not token_key:
                    continue
                if token_key in normalized_names:
                    return f"{other_id}:{_single_line(profile.get('name'), 40) or token}"
                for name_key in normalized_names:
                    if len(name_key) >= 3 and len(token_key) >= 3 and (name_key in token_key or token_key in name_key):
                        return f"{other_id}:{_single_line(profile.get('name'), 40) or token}"
        return ""

    def _worldbook_registration_pending_map(self, group: dict[str, Any]) -> dict[str, Any]:
        pending = group.setdefault("worldbook_registration_confirmations", {})
        if not isinstance(pending, dict):
            pending = {}
            group["worldbook_registration_confirmations"] = pending
        now = _now_ts()
        for user_id in list(pending.keys()):
            item = pending.get(user_id)
            if not isinstance(item, dict) or now - _safe_float(item.get("created_ts"), 0) > 6 * 60:
                pending.pop(user_id, None)
        return pending

    @staticmethod
    def _worldbook_registration_confirmation_intent(text: str) -> str:
        cleaned = _single_line(text, 80)
        cleaned = re.sub(r"\[CQ:at,[^\]]+\]", " ", cleaned)
        cleaned = re.sub(r"@\S+", " ", cleaned).strip()
        if not cleaned:
            return ""
        if re.search(r"(不行|不是|别|不要|算了|错了|不对|改一下|等等|拒绝)", cleaned):
            return "reject"
        if re.search(r"^(可以|可|行|好|好的|嗯|嗯嗯|对|对的|是|是的|没错|叫吧|就这样|可以呀|可以啊)[。！？!?\s]*$", cleaned):
            return "accept"
        return ""

    def _create_worldbook_self_registration_profile(
        self,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        name: str,
        aliases: list[str],
        group: dict[str, Any],
    ) -> dict[str, Any]:
        profiles = self.data.setdefault("worldbook_member_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["worldbook_member_profiles"] = profiles
        deleted = self.data.get("worldbook_deleted_member_ids")
        if isinstance(deleted, list) and sender_id in deleted:
            self.data["worldbook_deleted_member_ids"] = [item for item in deleted if str(item) != sender_id]
        content = f"{name}在群 {group_id} 主动向 Bot 自我介绍。"
        profile = {
            "user_id": sender_id,
            "name": name,
            "gender": "",
            "aliases": aliases,
            "content": content,
            "identity_note": f"QQ {sender_id}，自称{name}。",
            "boundary_note": "",
            "important_memories": [],
            "enabled": True,
            "priority": 120,
            "source_entries": ["群聊自登记"],
            "observed_names": [item for item in [_single_line(sender_name, 40)] if item and item not in aliases],
            "auto_registered_ts": _now_ts(),
            "auto_registration_pending": True,
            "self_intro_text": _single_line(text, 260),
        }
        profiles[sender_id] = profile
        recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
        logger.info(
            "群聊关系网自登记节点: group=%s user=%s name=%s aliases=%s",
            group_id or "-",
            sender_id,
            name,
            "、".join(aliases) or "-",
        )
        return {
            "group_id": str(group_id),
            "user_id": sender_id,
            "name": name,
            "aliases": aliases,
            "text": _single_line(text, 260),
            "sender_name": _single_line(sender_name, 40),
            "recent": [dict(item) for item in recent[-6:] if isinstance(item, dict)],
        }

    def _worldbook_self_registration_block_word_hit(self, *texts: Any) -> str:
        raw_words = runtime_persona_setting(self, "worldbook_self_registration_block_words", "")
        if isinstance(raw_words, str):
            parser = getattr(self, "_parse_text_list_config", None)
            raw_words = (
                parser(raw_words, limit=120)
                if callable(parser)
                else [item.strip() for item in re.split(r"[\n,，、;；]+", raw_words) if item.strip()][:120]
            )
        if not isinstance(raw_words, list) or not raw_words:
            return ""
        haystacks = []
        for text in texts:
            normalized = _single_line(text, 80)
            if not normalized:
                continue
            haystacks.append(normalized.lower())
            haystacks.append(re.sub(r"\s+", "", normalized).lower())
        for raw_word in raw_words:
            word = _single_line(raw_word, 40)
            if not word:
                continue
            lowered = word.lower()
            compact = re.sub(r"\s+", "", lowered)
            for haystack in haystacks:
                if lowered and lowered in haystack:
                    return word
                if compact and compact in haystack:
                    return word
        return ""

    def _worldbook_self_registration_block_reply_text(self) -> str:
        reply = _single_line(
            runtime_persona_setting(self, "worldbook_self_registration_block_reply", "这个称呼我不记。"),
            80,
        )
        if reply in {"这个称呼我先不记。", "你是小猪"}:
            reply = ""
        return reply or "这个称呼我不记。"

    def _extract_worldbook_self_intro(self, text: str) -> dict[str, Any] | None:
        cleaned = str(text or "")
        cleaned = re.sub(r"\[CQ:at,[^\]]+\]", " ", cleaned)
        bot_name = runtime_persona_setting(self, "bot_name", "小星")
        if bot_name:
            cleaned = cleaned.replace(bot_name, " ")
        cleaned = re.sub(r"@\S+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned or len(cleaned) > 20:
            return None
        # “我是说……” is a discourse correction, not a self-introduction.
        # Accept spacing and punctuation variants before applying the strict
        # self-introduction grammar below.
        discourse_probe = re.sub(r"\s+", "", cleaned)
        if re.search(
            r"(我是不是|我不是|我(?:是)?说|我是想|我是觉得|我是因为|我是来|我是要|我是在|我是什么|我是谁|你觉得我是)",
            discourse_probe,
        ):
            return None
        if not re.search(r"^(?:我是|我叫|叫我|以后叫我|可以叫我|你可以叫我)\S{1,24}(?:\s*(?:你可以叫我|可以叫我|以后叫我|叫我)\S{1,16})?[。！？!?\s]*$", cleaned):
            return None
        aliases: list[str] = []
        primary = ""
        attempted = False
        for match in re.finditer(r"^(?:我是|我叫)([\u4e00-\u9fffA-Za-z0-9_·・\-]{1,8})(?=$|[。！？!?\s,，、]|你可以叫我|可以叫我|以后叫我|叫我)", cleaned):
            attempted = True
            name = self._normalize_worldbook_self_name(match.group(1))
            if name:
                primary = primary or name
                aliases.append(name)
        for match in re.finditer(r"(?:你可以叫我|可以叫我|以后叫我|叫我)([^。！？\n]{1,32})[。！？!?\s]*$", cleaned):
            attempted = True
            raw = match.group(1)
            raw = re.split(r"(?:就行|好了|谢谢|麻烦|$)", raw, maxsplit=1)[0]
            for part in re.split(r"(?:或者|还是|和|或|、|/|,|，|;|；|\s+)", raw):
                name = self._normalize_worldbook_self_name(part)
                if name:
                    primary = primary or name
                    aliases.append(name)
        aliases = list(dict.fromkeys(item for item in aliases if item))
        if not primary and aliases:
            primary = aliases[0]
        if not primary:
            return {"blocked": True} if attempted else None
        return {"name": primary, "aliases": aliases[:8]}

    def _maybe_worldbook_self_register_from_group_message(
        self,
        event: AstrMessageEvent,
        *,
        group_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        group: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not (
            runtime_persona_setting(self, "enable_worldbook_member_recognition", True)
            and runtime_persona_setting(self, "worldbook_self_registration", True)
        ):
            return None
        sender_id = str(sender_id or "").strip()
        if not sender_id:
            return None
        pending = self._worldbook_registration_pending_map(group)
        pending_item = pending.get(sender_id)
        if isinstance(pending_item, dict):
            intent = self._worldbook_registration_confirmation_intent(text)
            if intent == "reject":
                pending.pop(sender_id, None)
                return {"confirm_reply": "好，那我不记。"}
            if intent == "accept":
                name = _single_line(pending_item.get("name"), 40) or sender_id
                aliases = [
                    _single_line(item, 40)
                    for item in (pending_item.get("aliases") if isinstance(pending_item.get("aliases"), list) else [])
                    if _single_line(item, 40) and _single_line(item, 40) != sender_id
                ]
                block_word = self._worldbook_self_registration_block_word_hit(
                    name,
                    *aliases,
                    pending_item.get("text"),
                )
                if block_word:
                    pending.pop(sender_id, None)
                    logger.info(
                        "群聊关系网自登记确认时拒绝: group=%s user=%s name=%s reason=命中自登记屏蔽词 %s",
                        group_id or "-",
                        sender_id,
                        name,
                        block_word,
                    )
                    return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
                conflict = self._worldbook_self_registration_conflict(sender_id, [name, *aliases])
                if conflict:
                    pending.pop(sender_id, None)
                    logger.info(
                        "群聊关系网自登记确认时拒绝: group=%s user=%s name=%s reason=名称疑似冒领已有节点 %s",
                        group_id or "-",
                        sender_id,
                        name,
                        conflict,
                    )
                    return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
                pending.pop(sender_id, None)
                payload = self._create_worldbook_self_registration_profile(
                    group_id=group_id,
                    sender_id=sender_id,
                    sender_name=_single_line(pending_item.get("sender_name"), 40) or sender_name,
                    text=_single_line(pending_item.get("text"), 260) or text,
                    name=name,
                    aliases=aliases,
                    group=group,
                )
                payload["confirm_reply"] = f"好，那我记住你是{name}。"
                return payload
        existing_profiles = self.data.get("worldbook_member_profiles")
        existing_profile = existing_profiles.get(sender_id) if isinstance(existing_profiles, dict) else None
        if isinstance(existing_profile, dict) and not bool(existing_profile.get("observation_only")):
            return None
        if not self._group_message_explicitly_ats_bot(event):
            return None
        intro = self._extract_worldbook_self_intro(text)
        if not intro:
            return None
        if intro.get("blocked"):
            logger.info(
                "群聊关系网自登记已拒绝: group=%s user=%s reason=称呼不合规或超过六字",
                group_id or "-",
                sender_id,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        name = _single_line(intro.get("name"), 40) or sender_id
        aliases = [
            _single_line(item, 40)
            for item in (intro.get("aliases") if isinstance(intro.get("aliases"), list) else [])
            if _single_line(item, 40) and _single_line(item, 40) != sender_id
        ]
        block_word = self._worldbook_self_registration_block_word_hit(name, *aliases, text)
        if block_word:
            logger.info(
                "群聊关系网自登记已拒绝: group=%s user=%s name=%s reason=命中自登记屏蔽词 %s",
                group_id or "-",
                sender_id,
                name,
                block_word,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        conflict = self._worldbook_self_registration_conflict(sender_id, [name, *aliases])
        if conflict:
            logger.info(
                "群聊关系网自登记已拒绝: group=%s user=%s name=%s reason=名称疑似冒领已有节点 %s",
                group_id or "-",
                sender_id,
                name,
                conflict,
            )
            return {"blocked_reply": self._worldbook_self_registration_block_reply_text()}
        if isinstance(existing_profile, dict):
            if self._confirm_worldbook_observation_profile_name(
                existing_profile,
                sender_id=sender_id,
                name=name,
                aliases=aliases,
            ):
                return {"confirm_reply": f"好，以后我叫你{name}。", "updated_observation_profile": True}
        profiles = self.data.setdefault("worldbook_member_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["worldbook_member_profiles"] = profiles
        pending[sender_id] = {
            "name": name,
            "aliases": aliases,
            "text": _single_line(text, 260),
            "sender_name": _single_line(sender_name, 40),
            "created_ts": _now_ts(),
        }
        logger.info(
            "群聊关系网自登记待确认: group=%s user=%s name=%s aliases=%s",
            group_id or "-",
            sender_id,
            name,
            "、".join(aliases) or "-",
        )
        return {"confirm_reply": f"那我以后叫你{name}可以吗？"}
