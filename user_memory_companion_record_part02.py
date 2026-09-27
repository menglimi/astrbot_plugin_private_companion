# -*- coding: utf-8 -*-
"""UserMemoryCompanionRecordPart02Mixin。

由 tools/split_mixin_domain.py 从 user_memory_companion_record.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 381 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryCompanionRecordMixin）。
"""
from __future__ import annotations
from .user_memory_companion_record_shared import Any
from .user_memory_companion_record_shared import _now_ts
from .user_memory_companion_record_shared import _safe_float
from .user_memory_companion_record_shared import _single_line
from .user_memory_companion_record_shared import datetime
from .user_memory_companion_record_shared import re
from .user_memory_companion_record_shared import runtime_persona_setting



class UserMemoryCompanionRecordPart02Mixin:
    """UserMemoryCompanionRecordPart02Mixin（从 UserMemoryCompanionRecordMixin 拆出）。"""


    def _format_dialogue_episodes_for_prompt(self, user: dict[str, Any], *, hint: str = "") -> str:
        episodes = user.get("dialogue_episodes")
        if not isinstance(episodes, list):
            return ""
        lines: list[str] = []
        for item in self._select_dialogue_episodes_for_prompt(episodes, hint=hint, limit=3):
            summary = _single_line(item.get("summary"), 120)
            if not summary:
                continue
            mood = _single_line(item.get("emotional_residue"), 60)
            topic = _single_line(item.get("reusable_topic"), 80)
            parts = [summary]
            if mood:
                parts.append(f"当时留下的感觉是{mood}")
            if topic:
                parts.append(f"可以顺手接回{topic}")
            lines.append("- " + "；".join(parts))
        return "\n".join(lines)

    def _open_loop_relevance_score(self, item: dict[str, Any], *, hint: str = "") -> float:
        text = _single_line(item.get("text"), 120)
        if not text:
            return 0.0
        score = 0.0
        created_ts = _safe_float(item.get("created_ts"), 0)
        if created_ts > 0:
            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
            if age_hours <= 24:
                score += 2.0
            elif age_hours <= 168:
                score += 1.0
        status = str(item.get("status") or "")
        if status in {"已完成", "已取消"}:
            score -= 8.0
        hint_text = _single_line(hint, 260).lower()
        if hint_text:
            searchable = text.lower()
            tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", hint_text)
            for token in dict.fromkeys(tokens):
                if token and token in searchable:
                    score += 3.0
        return score

    @staticmethod
    def _open_loop_created_ts(item: dict[str, Any], fallback: float = 0.0) -> float:
        """Read both numeric and legacy readable timestamps for an open loop."""
        if not isinstance(item, dict):
            return fallback
        created_ts = _safe_float(item.get("created_ts"), 0.0)
        if created_ts > 0:
            return created_ts
        created_at = _single_line(item.get("created_at"), 40)
        if created_at:
            for value in (created_at, created_at.replace("Z", "+00:00")):
                try:
                    parsed = datetime.fromisoformat(value)
                    created_ts = parsed.timestamp()
                    if created_ts > 0:
                        return created_ts
                except (TypeError, ValueError, OverflowError):
                    continue
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    created_ts = datetime.strptime(created_at, fmt).timestamp()
                    if created_ts > 0:
                        return created_ts
                except (TypeError, ValueError, OverflowError):
                    continue
        return fallback

    @staticmethod
    def _format_open_loop_timestamp(created_ts: float, now: float | None = None) -> str:
        if created_ts <= 0:
            return ""
        current = _now_ts() if now is None else now
        age_seconds = max(0.0, current - created_ts)
        if age_seconds < 3600:
            age_text = "不到 1 小时"
        elif age_seconds < 86400:
            age_text = f"约 {max(1, int(age_seconds / 3600))} 小时"
        else:
            age_text = f"约 {max(1, int(age_seconds / 86400))} 天"
        return f"记录于 {datetime.fromtimestamp(created_ts).strftime('%Y-%m-%d %H:%M')}，距今{age_text}"

    def _open_loop_hint_allows_topic_return(self, hint: str) -> bool:
        cleaned = _single_line(hint, 260)
        if not cleaned:
            return False
        return bool(re.search(
            r"(刚才|刚刚|前面|之前|上次|上回|昨天|昨晚|那个|这个|继续|接着|回到|再说|讲讲|说说|展开|还没|没回答|没讲完|我问的|我刚问|你刚说)",
            cleaned,
        ))

    def _select_open_loops_for_prompt(
        self,
        loops: list[dict[str, Any]],
        *,
        hint: str = "",
        limit: int = 3,
        require_relevant: bool | None = None,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        total = len(loops)
        hint_text = _single_line(hint, 260)
        if require_relevant is None:
            require_relevant = bool(hint_text)
        for index, item in enumerate(loops):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            loop_text = _single_line(item.get("text"), 120)
            if not loop_text:
                continue
            topic_score = self._open_loop_match_score(loop_text, hint_text) if hint_text else 0.0
            # Generic callback words such as “之前/那个/继续” are not enough
            # to revive an old topic; the current message needs real topic overlap.
            if require_relevant and topic_score < 0.22:
                continue
            score = self._open_loop_relevance_score(item, hint=hint)
            if index >= max(0, total - 1):
                score += 2.0
            elif index >= max(0, total - 3):
                score += 1.0
            score += topic_score * 4.0
            candidates.append((index, score, item))
        if not candidates:
            return []
        picked = sorted(candidates, key=lambda part: (part[1], part[0]), reverse=True)[: max(1, limit)]
        return [item for _, _, item in sorted(picked, key=lambda part: part[0])]

    def _format_open_loops_for_prompt(self, user: dict[str, Any], *, hint: str = "") -> str:
        loops = user.get("open_loops")
        if not isinstance(loops, list):
            return ""
        lines: list[str] = []
        now = _now_ts()
        kept = []
        seen: set[str] = set()
        for item in loops:
            if not isinstance(item, dict):
                continue
            created_ts = self._open_loop_created_ts(item, now)
            if created_ts > 0 and now - created_ts > 14 * 86400:
                continue
            if not _safe_float(item.get("created_ts"), 0):
                item["created_ts"] = created_ts
            if not _single_line(item.get("created_at"), 40) and created_ts > 0:
                item["created_at"] = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S")
            signature = self._memory_fact_signature(item.get("text"))
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            kept.append(item)
        if len(kept) != len(loops):
            user["open_loops"] = kept[-12:]
        for item in self._select_open_loops_for_prompt(kept, hint=hint, limit=3):
            text = self._naturalize_open_loop_text(item.get("text"))
            if not text:
                continue
            status = _single_line(item.get("status"), 30) or "待自然延续"
            created_ts = self._open_loop_created_ts(item, now)
            timestamp = self._format_open_loop_timestamp(created_ts, now)
            suffix = f"（{timestamp}）" if timestamp else ""
            if status == "待自然延续":
                lines.append(f"- 之前还留着{suffix}：{text}")
            else:
                lines.append(f"- {status}{suffix}：{text}")
        return "\n".join(lines)

    def _naturalize_open_loop_text(self, raw: Any) -> str:
        text = _single_line(raw, 100)
        if not text:
            return ""
        text = re.sub(r"^(?:记得|帮我|提醒我|到时候|以后|明天|今晚|等会儿|一会儿)[，,：:\s]*", "", text)
        text = re.sub(r"(?:你记一下|你记住|别忘了)[。！？!?,，\s]*$", "", text)
        return _single_line(text.strip(" ：:，,。"), 90)

    def _extract_explicit_open_loop_from_message(self, text: str) -> str:
        cleaned = _single_line(text, 260)
        if not cleaned:
            return ""
        if self._is_structured_or_diagnostic_text(cleaned):
            return ""
        weak_only = ("到时候", "以后", "明天", "今晚", "等会儿", "一会儿")
        has_strong_marker = bool(re.search(r"(提醒我|帮我记|帮我提醒|你记一下|你记住|别忘了|记得提醒|记得叫|记得喊|到点叫|到点提醒)", cleaned))
        if not has_strong_marker:
            return ""
        patterns = (
            r"(?:提醒我|帮我提醒|记得提醒|到点提醒|到点叫|记得叫|记得喊)([^。！？\n]{2,90})",
            r"(?:帮我记|你记一下|你记住|别忘了|记得)([^。！？\n]{2,90})",
            r"([^。！？\n]{2,90})(?:你记一下|你记住|别忘了)",
        )
        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if not match:
                continue
            candidate = self._naturalize_open_loop_text(match.group(0))
            if not candidate:
                continue
            if candidate in weak_only:
                continue
            if len(candidate) < 3:
                continue
            return candidate
        return ""

    def _open_loop_match_score(self, loop_text: str, inbound_text: str) -> float:
        loop = self._compact_repeat_text(loop_text)
        inbound = self._compact_repeat_text(inbound_text)
        if not loop or not inbound:
            return 0.0
        if len(loop) >= 4 and loop in inbound:
            return 1.0
        if len(inbound) >= 4 and inbound in loop:
            return 0.9
        stopwords = {
            "之前", "以前", "上次", "上回", "那个", "这个", "继续", "接着", "后来", "怎么样",
            "还有", "一下", "之后", "提醒", "记得", "帮我", "事情", "话题",
        }

        def _topic_tokens(value: str) -> set[str]:
            tokens = set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", value))
            for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", value):
                tokens.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
                if len(sequence) >= 4:
                    tokens.update(sequence[index:index + 3] for index in range(len(sequence) - 2))
            return {token for token in tokens if token not in stopwords}

        loop_tokens = _topic_tokens(loop_text)
        inbound_tokens = _topic_tokens(inbound_text)
        if not loop_tokens or not inbound_tokens:
            return 0.0
        overlap = len(loop_tokens & inbound_tokens)
        score = overlap / max(1, min(len(loop_tokens), len(inbound_tokens)))
        # Chinese conversational follow-ups often mention only one concrete
        # subject word; preserve that signal without allowing generic words.
        if overlap and any(len(token) >= 2 for token in loop_tokens & inbound_tokens):
            score = max(score, 0.25)
        return score

    def _resolve_matching_open_loop(self, loops: list[Any], text: str) -> dict[str, Any] | None:
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for index, item in enumerate(loops):
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") in {"已完成", "已取消"}:
                continue
            loop_text = _single_line(item.get("text"), 120)
            if not loop_text:
                continue
            score = self._open_loop_match_score(loop_text, text)
            candidates.append((score, index, item))
        if not candidates:
            return None
        score, _, item = max(candidates, key=lambda part: (part[0], part[1]))
        if score >= 0.34:
            return item
        # Short acknowledgements such as “好了/没事了” must not resolve an
        # unrelated historical loop merely because it happens to be newest.
        return None

    def _update_open_loops_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_open_loop_tracking", True):
            return
        cleaned = _single_line(text, 260)
        if not cleaned:
            return
        loops = user.setdefault("open_loops", [])
        if not isinstance(loops, list):
            loops = []
            user["open_loops"] = loops

        now = _now_ts()
        created_at = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        completion_markers = ("好了", "搞定", "解决了", "完成了", "不用了", "取消", "算了", "没事了", "不用提醒")
        if loops and any(marker in cleaned for marker in completion_markers):
            item = self._resolve_matching_open_loop(loops, cleaned)
            if item is not None:
                item["status"] = "已取消" if any(marker in cleaned for marker in ("不用了", "取消", "算了", "不用提醒")) else "已完成"
                item["resolved_ts"] = _now_ts()

        loop_text = self._extract_explicit_open_loop_from_message(cleaned)
        if loop_text:
            existing = {_single_line(item.get("text"), 120) for item in loops if isinstance(item, dict)}
            if loop_text not in existing:
                loops.append(
                    {
                        "text": loop_text,
                        "status": "待自然延续",
                        "created_ts": now,
                        "created_at": created_at,
                        "source": "user_message",
                    }
                )
        del loops[:-12]

    def _remove_open_loop_entry(self, user: dict[str, Any], value: str) -> str:
        loops = user.get("open_loops")
        if not isinstance(loops, list) or not loops:
            user["open_loops"] = []
            return "当前没有未完话头。"

        keyword = _single_line(value, 60)
        if not keyword:
            return "请提供要删除的话头关键词，或用“全部”清空所有未完话头。"

        if keyword.lower() in {"全部", "所有", "all", "清空"}:
            kept_pending: list[dict[str, Any]] = []
            removed_count = 0
            for item in loops:
                if isinstance(item, dict) and str(item.get("status") or "") in {"已完成", "已取消"}:
                    kept_pending.append(item)
                else:
                    removed_count += 1
            user["open_loops"] = kept_pending[-12:]
            return f"已清空 {removed_count} 条未完话头。" if removed_count else "当前没有未完话头。"

        if len(keyword) < 2:
            return "关键词太短，请提供至少 2 个字，避免误删多条话头。"

        kept: list[dict[str, Any]] = []
        removed: list[str] = []
        for item in loops:
            if not isinstance(item, dict):
                continue
            text = _single_line(item.get("text"), 120)
            if text and keyword in text and str(item.get("status") or "") not in {"已完成", "已取消"}:
                removed.append(text)
            else:
                kept.append(item)
        user["open_loops"] = kept[-12:]
        if not removed:
            return "没有找到匹配的未完话头。"
        return "已删除未完话头：\n" + "\n".join(f"- {item}" for item in removed)

    async def _collect_recent_private_conversation_text(
        self,
        user: dict[str, Any],
        *,
        hours: int = 24,
        max_lines: int = 80,
    ) -> str:
        umo = str(user.get("umo") or "").strip()
        if not umo:
            return ""
        try:
            conv_id = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if not conv_id:
                return ""
            conv = await self.context.conversation_manager.get_conversation(umo, conv_id)
        except Exception:
            return ""
        history = self._load_conversation_history_items(conv)
        if not history:
            return ""
        now = _now_ts()
        cutoff = now - max(1, hours) * 3600
        lines: list[str] = []
        for item in history:
            line = self._format_history_item_for_summary(item)
            if not line:
                continue
            ts = self._history_item_timestamp(item)
            if ts is not None and ts < cutoff:
                continue
            lines.append(line)
        if not lines:
            lines = [self._format_history_item_for_summary(item) for item in history[-max_lines:]]
            lines = [line for line in lines if line]
        return "\n".join(lines[-max_lines:]).strip()

    def _normalize_string_list(self, raw: Any, *, limit: int = 6, item_limit: int = 90) -> list[str]:
        if isinstance(raw, list):
            values = raw
        elif raw:
            values = [raw]
        else:
            values = []
        result = []
        for value in values:
            text = _single_line(value, item_limit)
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    async def _maybe_refresh_dialogue_episode(self, user_id: str, user: dict[str, Any]) -> None:
        if not runtime_persona_setting(self, "enable_dialogue_episode_memory", True):
            return
        now = _now_ts()
        async with self._req041_person_write_lock(self._req041_private_memory_person_key(user_id)):
            await self._refresh_dialogue_episode_batch(user_id, user, now)
        return
