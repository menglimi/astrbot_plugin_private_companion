# -*- coding: utf-8 -*-
"""AtRelayPart02Mixin。

由 tools/split_mixin_domain.py 从 atrelay.py 机械抽取（23 个方法 + 0 个模块级名字 + 0 个类级赋值 / 469 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 AtRelayMixin）。
"""
from __future__ import annotations

from .atrelay_shared import _render_atrelay_prompt_section_labeled, logger
from .atrelay_shared import Any
from .atrelay_shared import AstrMessageEvent
from .atrelay_shared import PromptSection
from .atrelay_shared import _now_ts
from .atrelay_shared import _safe_float
from .atrelay_shared import _single_line
from .atrelay_shared import normalize_legacy_tag_text
from .atrelay_shared import prompt_section
from .atrelay_shared import re



class AtRelayPart02Mixin:
    """AtRelayPart02Mixin（从 AtRelayMixin 拆出）。"""


    def _atrelay_sensitive_reason(self, text: str) -> str:
        cleaned = _single_line(text, 800)
        if not cleaned:
            return ""
        rules = [
            ("私密内容", ("秘密", "私下说", "私下聊", "私密", "隐私", "别告诉", "不要告诉", "只告诉你", "密码", "口令", "账号")),
            ("强情绪内容", ("讨厌", "恨", "烦死", "恶心", "滚", "闭嘴", "傻逼", "废物", "垃圾", "绝交", "再也不")),
            ("关系或告白内容", ("喜欢你", "爱你", "暗恋", "告白", "表白", "分手", "复合", "吃醋", "想你")),
            ("金钱或求助内容", ("借钱", "还钱", "转账", "红包", "欠我", "欠钱", "救命", "报警")),
            ("身体健康内容", ("生病", "医院", "抑郁", "自残", "想死", "怀孕", "生理期", "身体")),
        ]
        for label, tokens in rules:
            if any(token in cleaned for token in tokens):
                return label
        if re.search(r"(你|他|她).{0,8}(怎么|凭什么|是不是|到底).{0,20}(烦|讨厌|过分|有病|恶心)", cleaned):
            return "带情绪的质问"
        return ""

    def _atrelay_event_confirms_sensitive_send(self, event: AstrMessageEvent) -> bool:
        message_text = _single_line(getattr(event, "message_str", ""), 220)
        if not message_text:
            return False
        confirms = (
            "随便", "都行", "可以", "发吧", "就这样", "原样", "原话", "照发",
            "委婉", "换种说法", "你看着办", "直接发", "确认", "没事",
        )
        if not any(token in message_text for token in confirms):
            return False
        quote_text = ""
        try:
            for seg in getattr(getattr(event, "message_obj", None), "message", []) or []:
                for attr in ("text", "message", "raw_message", "content"):
                    value = getattr(seg, attr, None)
                    if value:
                        quote_text += " " + str(value)
                data = getattr(seg, "data", None)
                if isinstance(data, dict):
                    quote_text += " " + " ".join(str(data.get(key) or "") for key in ("text", "message", "raw_message", "content"))
        except Exception:
            quote_text = ""
        combined = f"{message_text} {quote_text}"
        return any(token in combined for token in ("原样发", "原样带", "换种说法", "委婉一点", "直接转述", "不能直接转述"))

    def _atrelay_boundary_reason(self, text: str) -> str:
        cleaned = _single_line(text, 800)
        if not cleaned:
            return ""
        insult_tokens = ("骂他", "骂她", "骂你", "傻逼", "废物", "垃圾", "滚", "闭嘴", "爬", "有病", "恶心")
        if any(token in cleaned for token in insult_tokens):
            return "包含辱骂或攻击"
        impersonation_patterns = (
            r"我是.{0,6}(群主|管理员|管理|老师|本人|官方)",
            r"(冒充|假装|装成|伪装成|替我装)",
            r"(告诉|跟).{0,12}(我是|你是).{0,8}(群主|管理员|管理|老师|官方)",
        )
        if any(re.search(pattern, cleaned) for pattern in impersonation_patterns):
            return "涉及身份冒充或虚假权限"
        if re.search(r"(密码|口令|验证码|账号).{0,12}(告诉|发给|转给|问|要)", cleaned):
            return "涉及敏感凭据"
        if re.search(r"(威胁|恐吓|打他|弄他|开盒|人肉|盒了)", cleaned):
            return "包含威胁或骚扰"
        return ""

    def _atrelay_boundary_guard(self, text: str) -> str:
        reason = self._atrelay_boundary_reason(text)
        if not reason:
            return ""
        return f"发送失败：这条转述{reason}，不能代发。可以改成中性提醒后再让我发送。"

    def _atrelay_confirmation_guard(self, text: str, *, relay_mode: str, sensitive_confirmed: bool) -> str:
        if not getattr(self, "atrelay_sensitive_confirm", True):
            return ""
        reason = self._atrelay_sensitive_reason(text)
        if not reason or sensitive_confirmed:
            return ""
        return (
            f"需要先确认：这句话像{reason}，不能直接转述。"
            "请先问用户“要我原样带过去，还是稍微委婉一点？”确认后再发送。"
        )

    @staticmethod
    def _atrelay_bool_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "on", "确认", "已确认", "可以", "是"}

    def _normalize_atrelay_text(self, text: Any, *, limit: int = 800, label: bool = False) -> str:
        return _single_line(normalize_legacy_tag_text(text, label=label), limit)

    def _parse_atrelay_target_list(self, value: Any, *, limit: int | None = None) -> list[str]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = re.split(r"[\n,，、;；\s]+", str(value or ""))
        items: list[str] = []
        for item in raw_items:
            text = _single_line(item, 80)
            if text and text not in items:
                items.append(text)
            if limit and len(items) >= limit:
                break
        return items

    def _normalize_atrelay_private_target_id(self, value: Any) -> str:
        normalizer = getattr(self, "_normalize_private_identity_id", None)
        candidate = normalizer(value) if callable(normalizer) else _single_line(value, 128)
        if not candidate or self._is_bot_self_user_id(candidate):
            return ""
        if candidate.isdigit():
            return candidate
        canonical = self._canonical_private_user_id(candidate)
        configured = set(self._configured_target_ids()) if callable(getattr(self, "_configured_target_ids", None)) else set()
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        users = users if isinstance(users, dict) else {}
        aliases = getattr(self, "private_user_aliases", {}) or {}
        deliveries = getattr(self, "private_user_delivery_aliases", {}) or {}
        known_ids = {candidate, canonical}
        for mapping in (aliases, deliveries):
            if isinstance(mapping, dict):
                for key, target in mapping.items():
                    for raw in (key, target):
                        normalized = normalizer(raw) if callable(normalizer) else _single_line(raw, 128)
                        if normalized:
                            known_ids.add(normalized)
        if configured.intersection(known_ids):
            return canonical or candidate
        if any(item in users for item in known_ids):
            return canonical if canonical in users else candidate
        return ""

    def _atrelay_send_log(self) -> list[dict[str, Any]]:
        log = self.data.setdefault("atrelay_send_log", [])
        if not isinstance(log, list):
            log = []
            self.data["atrelay_send_log"] = log
        cutoff = _now_ts() - 24 * 3600
        kept = [item for item in log if isinstance(item, dict) and _safe_float(item.get("ts"), 0) >= cutoff]
        if len(kept) != len(log):
            self.data["atrelay_send_log"] = kept
        return kept

    def _atrelay_send_signature(self, kind: str, target: str, text: str, at_user: str = "") -> str:
        compact = re.sub(r"\s+", "", self._normalize_atrelay_text(text, limit=240))
        return f"{kind}:{target}:{at_user}:{compact[:160]}"

    def _atrelay_cached_group_matches(self, hint: Any = "") -> list[dict[str, str]]:
        query = _single_line(hint, 80)
        query_variants = [query] if query else [""]
        if query:
            relaxed = re.sub(r"(什么的|那个|这个|群聊|群里|群|里面|里|吧|吗|呀|啊|呢)$", "", query).strip()
            if relaxed and relaxed not in query_variants:
                query_variants.append(relaxed)
        matches: dict[str, dict[str, str]] = {}

        def add(group_id: Any, name: Any = "", source: str = "") -> None:
            gid = _single_line(group_id, 40)
            if not gid:
                return
            label = _single_line(name, 100) or gid
            existing = matches.setdefault(gid, {"group_id": gid, "group_name": label, "source": source})
            if label and (not existing.get("group_name") or existing.get("group_name") == gid):
                existing["group_name"] = label
            if source and not existing.get("source"):
                existing["source"] = source

        groups = self.data.get("groups") if isinstance(self.data.get("groups"), dict) else {}
        for group_id, group in groups.items():
            if not isinstance(group, dict):
                continue
            gid = _single_line(group.get("group_id") or group_id, 40)
            tokens = [
                gid,
                group.get("name"),
                group.get("group_name"),
                group.get("display_name"),
                group.get("nickname"),
            ]
            clean_tokens = [_single_line(token, 100) for token in tokens if _single_line(token, 100)]
            if not query or any(q and (q == token or q in token or token in q) for q in query_variants for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "plugin_group")

        profiles = self.data.get("worldbook_group_profiles") if isinstance(self.data.get("worldbook_group_profiles"), dict) else {}
        for group_id, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            gid = _single_line(profile.get("group_id") or group_id, 40)
            tokens = [gid, profile.get("name"), profile.get("title"), profile.get("display_name")]
            clean_tokens = [_single_line(token, 100) for token in tokens if _single_line(token, 100)]
            if not query or any(q and (q == token or q in token or token in q) for q in query_variants for token in clean_tokens):
                label = next((token for token in clean_tokens if token and token != gid), gid)
                add(gid, label, "worldbook_group")
        return list(matches.values())[:12]

    async def _resolve_atrelay_target_group(self, event: AstrMessageEvent, group_hint: Any = "") -> dict[str, Any]:
        hint = _single_line(group_hint, 160)
        direct_group_id = self._normalize_atrelay_group_target_id(hint)
        is_group_umo = ":GroupMessage:" in hint
        if direct_group_id and (
            direct_group_id.isdigit()
            or is_group_umo
            or direct_group_id in self._atrelay_known_group_ids()
        ):
            return {"status": "success", "group_id": direct_group_id, "source": "direct"}
        current_group = self._extract_group_id_from_event(event)
        if not hint and current_group:
            return {"status": "success", "group_id": current_group, "source": "current_group"}
        configured_groups = [str(item) for item in self._configured_group_ids() if str(item or "").strip()]
        if not hint and len(configured_groups) == 1:
            return {"status": "success", "group_id": configured_groups[0], "source": "single_configured_group"}
        if not hint:
            return {"status": "need_group", "message": "需要补充目标群号或群名"}
        cached_matches = self._atrelay_cached_group_matches(hint)
        if len(cached_matches) == 1:
            match = cached_matches[0]
            logger.info(
                "跨群转述群名命中本地缓存: hint=%s group=%s source=%s",
                hint,
                match.get("group_id"),
                match.get("source"),
            )
            return {"status": "success", **match}
        if len(cached_matches) > 1:
            return {"status": "ambiguous", "matches": cached_matches[:8], "message": "匹配到多个群，请补充群号"}
        bot = getattr(event, "bot", None)
        api = getattr(bot, "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return {"status": "need_group", "message": "当前平台不能查询群列表，请直接提供群号"}
        try:
            groups = await call_action("get_group_list")
        except Exception as exc:
            return {"status": "error", "message": f"获取群列表失败: {_single_line(exc, 120)}"}
        matches = []
        for item in groups if isinstance(groups, list) else []:
            group_id = str(item.get("group_id") or "")
            name = _single_line(item.get("group_name") or item.get("group_remark"), 100)
            if hint in group_id or hint in name:
                matches.append({"group_id": group_id, "group_name": name})
        if len(matches) == 1:
            return {"status": "success", **matches[0], "source": "group_list"}
        if len(matches) > 1:
            return {"status": "ambiguous", "matches": matches[:8], "message": "匹配到多个群，请补充群号"}
        return {"status": "not_found", "message": "未找到匹配群聊"}

    def _atrelay_duplicate_guard(self, kind: str, target: str, text: str, at_user: str = "") -> str:
        signature = self._atrelay_send_signature(kind, target, text, at_user)
        now = _now_ts()
        for item in self._atrelay_send_log():
            if item.get("signature") == signature and now - _safe_float(item.get("ts"), 0) < 10 * 60:
                return "发送失败：近 10 分钟内已经发送过相同转述，已拦截重复发送。"
        return ""

    def _recent_atrelay_contexts(self) -> list[dict[str, Any]]:
        contexts = self.data.setdefault("recent_atrelay_contexts", [])
        if not isinstance(contexts, list):
            contexts = []
            self.data["recent_atrelay_contexts"] = contexts
        now = _now_ts()
        kept = [
            item for item in contexts
            if isinstance(item, dict) and now - _safe_float(item.get("ts"), 0) < 2 * 3600
        ]
        if len(kept) != len(contexts):
            contexts[:] = kept
        return contexts

    def _atrelay_source_snapshot_for_event(self, event: AstrMessageEvent | None) -> tuple[str, str]:
        if event is None:
            return "", ""
        source_user = self._atrelay_event_user_id(event)
        try:
            source_name = self._atrelay_identity_label(source_user, self._sender_display_name(event))
        except Exception:
            source_name = self._atrelay_identity_label(source_user)
        return _single_line(source_user, 40), _single_line(source_name, 80)

    def _note_atrelay_recent_context(
        self,
        *,
        kind: str,
        target: str,
        text: str,
        at_user: str = "",
        source_user: str = "",
        source_name: str = "",
    ) -> None:
        target_id = _single_line(target, 40)
        sent_text = self._normalize_atrelay_text(text, limit=300)
        if not target_id or not sent_text:
            return
        contexts = self._recent_atrelay_contexts()
        item = {
            "ts": _now_ts(),
            "kind": _single_line(kind, 20),
            "target": target_id,
            "at_user": _single_line(at_user, 40),
            "source_user": _single_line(source_user, 40),
            "source_name": _single_line(source_name, 80),
            "text": sent_text,
        }
        signature = self._atrelay_send_signature(kind, target_id, sent_text, at_user)
        contexts[:] = [
            old for old in contexts
            if self._atrelay_send_signature(
                _single_line(old.get("kind"), 20),
                _single_line(old.get("target"), 40),
                _single_line(old.get("text"), 300),
                _single_line(old.get("at_user"), 40),
            ) != signature
        ]
        contexts.append(item)
        del contexts[:-80]

    def _format_recent_atrelay_context_for_prompt(
        self,
        *,
        kind: str,
        target: str,
        sender_id: str = "",
        current_text: str = "",
        limit: int = 2,
    ) -> str:
        section = self._format_recent_atrelay_context_prompt_section(
            kind=kind,
            target=target,
            sender_id=sender_id,
            current_text=current_text,
            limit=limit,
        )
        return _render_atrelay_prompt_section_labeled(section)

    def _format_recent_atrelay_context_prompt_section(
        self,
        *,
        kind: str,
        target: str,
        sender_id: str = "",
        current_text: str = "",
        limit: int = 2,
    ) -> PromptSection:
        target_id = _single_line(target, 40)
        sender = _single_line(sender_id, 40)
        kind = _single_line(kind, 20)
        asks_source = bool(re.search(r"(谁|哪位|哪个|谁让|谁叫|谁说|谁托|来源|发起人)", _single_line(current_text, 160)))
        lines: list[str] = []
        if target_id:
            for item in reversed(self._recent_atrelay_contexts()):
                if _single_line(item.get("kind"), 20) != kind:
                    continue
                if _single_line(item.get("target"), 40) != target_id:
                    continue
                at_user = _single_line(item.get("at_user"), 40)
                if sender and at_user and at_user != sender:
                    continue
                sent_text = _single_line(item.get("text"), 160)
                if not sent_text:
                    continue
                elapsed = self._format_timestamp_elapsed(_safe_float(item.get("ts"), 0))
                parts = [f"{elapsed}，你通过转述工具发出：{sent_text}"]
                if at_user:
                    parts.append(f"收话人 QQ:{at_user}")
                source_name = _single_line(item.get("source_name"), 60) if asks_source else ""
                if source_name:
                    parts.append(f"发起人:{source_name}")
                lines.append("｜".join(parts))
                if len(lines) >= max(1, limit):
                    break
        content = (
            "\n".join(f"- {line}" for line in lines)
            + "\n这些只用于理解对方为什么接话或道谢；不要主动复述工具名、内部记录或没必要说明来源。"
            if lines
            else ""
        )
        return prompt_section(
            key="atrelay.recent",
            title="刚刚的转述动作",
            source="atrelay",
            content=content,
        )
