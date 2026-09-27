# -*- coding: utf-8 -*-
"""UserMemoryCompanionRecordPart01Mixin。

由 tools/split_mixin_domain.py 从 user_memory_companion_record.py 机械抽取（16 个方法 + 0 个模块级名字 + 0 个类级赋值 / 475 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 UserMemoryCompanionRecordMixin）。
"""
from __future__ import annotations
from .user_memory_companion_record_shared import Any
from .user_memory_companion_record_shared import AuthoritativePrivateMemoryError
from .user_memory_companion_record_shared import AuthoritativePrivateMemoryStore
from .user_memory_companion_record_shared import _now_ts
from .user_memory_companion_record_shared import _safe_float
from .user_memory_companion_record_shared import _safe_int
from .user_memory_companion_record_shared import _single_line
from .user_memory_companion_record_shared import apply_private_memory_content
from .user_memory_companion_record_shared import asyncio
from .user_memory_companion_record_shared import datetime
from .user_memory_companion_record_shared import logger
from .user_memory_companion_record_shared import normalize_memory_items
from .user_memory_companion_record_shared import private_memory_content
from .user_memory_companion_record_shared import re
from .user_memory_companion_record_shared import relevant_memory_items
from .user_memory_companion_record_shared import runtime_persona_setting
from .user_memory_companion_record_shared import uuid



class UserMemoryCompanionRecordPart01Mixin:
    """UserMemoryCompanionRecordPart01Mixin（从 UserMemoryCompanionRecordMixin 拆出）。"""


    @staticmethod
    def _memory_fact_signature(text: Any) -> str:
        compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()
        return compact[:80]

    def _cleanup_companion_memory_items(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        memory = user.get("companion_memory")
        if not isinstance(memory, dict):
            return []
        items = normalize_memory_items(
            memory.get("items"),
            now=_now_ts(),
            max_items=runtime_persona_setting(self, "max_companion_memory_items", 36),
            signature_for=self._memory_fact_signature,
        )
        # This assignment is the compatibility persistence boundary: callers have
        # always observed the normalized list in companion_memory.items.
        memory["items"] = items
        return items

    def _companion_memory_relevant_items(self, user: dict[str, Any], *, hint: str = "", limit: int = 6) -> list[dict[str, Any]]:
        return relevant_memory_items(
            self._cleanup_companion_memory_items(user),
            hint=hint,
            limit=limit,
        )

    def _classify_companion_memory_candidate(self, cleaned: str) -> dict[str, Any]:
        lowered = cleaned.lower()
        explicit_tokens = (
            "记住", "记得", "以后", "一直", "永远", "长期", "固定", "默认",
            "不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线",
            "叫我", "我叫", "我生日", "我的生日", "生日是", "纪念日",
        )
        durable_tokens = (
            "以后", "一直", "永远", "长期", "固定", "默认",
            "不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线",
            "我生日", "我的生日", "生日是", "纪念日",
        )
        temporary_tokens = (
            "今天", "这次", "刚才", "刚刚", "现在", "此刻", "今晚", "明天",
            "最近", "暂时", "一会儿", "等会儿", "这会儿", "刚睡醒", "刚下课",
        )
        playful_endings = ("啦", "嘛", "呀", "哦", "捏", "www", "哈哈", "嘿嘿", "（", "(")
        memory_patterns = (
            "喜欢", "讨厌", "不喜欢", "别叫", "不要", "记住", "记得",
            "生日", "纪念日", "我是", "我叫", "叫我", "我在", "我住",
            "想要", "希望", "害怕", "雷点", "以后",
        )
        score = sum(1 for pattern in memory_patterns if pattern in cleaned or pattern in lowered)
        if score <= 0:
            return {"keep": False, "reason": "no_memory_signal"}
        explicit = any(token in cleaned for token in explicit_tokens)
        durable_explicit = any(token in cleaned for token in durable_tokens)
        is_temporary = any(token in cleaned for token in temporary_tokens)
        kind = "preference"
        if any(key in cleaned for key in ("不要", "别叫", "讨厌", "不喜欢", "雷点", "不许", "底线")):
            kind = "boundary"
        elif any(key in cleaned for key in ("生日", "纪念日", "以后", "记住", "记得")):
            kind = "important"
        if is_temporary and not explicit:
            return {"keep": False, "reason": "temporary_context"}
        if is_temporary and explicit and not durable_explicit:
            return {"keep": False, "reason": "temporary_soft_explicit"}
        if kind == "boundary":
            boundary_strong = any(token in cleaned for token in ("不要再", "别再", "以后别", "以后不要", "不许", "雷点", "底线", "讨厌", "不喜欢"))
            soft_boundary = (
                "别叫" in cleaned
                and not boundary_strong
                and any(cleaned.rstrip("。！？!?~～… ").endswith(token) for token in playful_endings)
            )
            if soft_boundary and not durable_explicit:
                return {"keep": False, "reason": "soft_playful_boundary"}
        if any(token in cleaned for token in ("开玩笑", "不是认真的", "随口", "口嗨")) and not explicit:
            return {"keep": False, "reason": "joke_or_uncertain"}
        weight = min(5, 1 + score + (2 if explicit else 0))
        return {"keep": True, "kind": kind, "weight": weight, "reason": "explicit" if explicit else "rule_match"}

    def _update_companion_memory_from_message(self, user: dict[str, Any], text: str) -> None:
        if not runtime_persona_setting(self, "enable_companion_memory", True):
            return
        cleaned = _single_line(text, 260)
        if not cleaned:
            return
        birthday_asked_at = _safe_float(user.get("birthday_curiosity_asked_at"), 0)
        asked_recently = birthday_asked_at > 0 and _now_ts() - birthday_asked_at <= 14 * 24 * 3600
        if asked_recently and re.search(r"(?:不想|不愿|不方便|先不|暂时不|别).{0,10}(?:说|讲|提|问)?.{0,6}生日|生日.{0,12}(?:不想|不愿|不方便|别|不要)", cleaned):
            user["birthday_curiosity_opt_out"] = True
            user["birthday_curiosity_asked_at"] = 0
        else:
            birthday_match = re.search(r"(?:(农历|公历)\s*)?(\d{1,2})\s*(?:月|[-./])\s*(\d{1,2})\s*(?:日|号)?", cleaned)
            explicit_birthday = bool(re.search(r"(?:我|我的|本人).{0,6}生日(?:.{0,10}(?:是|在|：|:))?", cleaned))
            if birthday_match and (asked_recently or explicit_birthday):
                user["birthday_profile"] = {
                    "calendar": "lunar" if birthday_match.group(1) == "农历" else "solar",
                    "month": int(birthday_match.group(2)),
                    "day": int(birthday_match.group(3)),
                    "raw": birthday_match.group(0),
                    "source": "birthday_curiosity_reply" if asked_recently else "user_explicit",
                    "confirmed_at": _now_ts(),
                }
                if asked_recently:
                    user["birthday_curiosity_answered_at"] = _now_ts()
                    user["birthday_curiosity_asked_at"] = 0
        memory = user.setdefault("companion_memory", {})
        if not isinstance(memory, dict):
            memory = {}
            user["companion_memory"] = memory
        raw_items = memory.get("items")
        items = raw_items if isinstance(raw_items, list) else []
        candidate = self._classify_companion_memory_candidate(cleaned)
        if not candidate.get("keep"):
            return
        item = {
            "text": cleaned,
            "kind": candidate.get("kind") or "preference",
            "weight": _safe_int(candidate.get("weight"), 1, 1, 5),
            "reason": candidate.get("reason") or "rule_match",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "created_ts": _now_ts(),
        }
        signature = self._memory_fact_signature(cleaned)
        deduped = [
            old
            for old in items
            if isinstance(old, dict) and self._memory_fact_signature(_single_line(old.get("text"), 260)) != signature
        ]
        deduped.insert(0, item)
        memory["items"] = deduped[: runtime_persona_setting(self, "max_companion_memory_items", 36)]
        memory["updated_at"] = item["created_at"]

    def _req041_private_memory_write_allowed(self, user: dict[str, Any]) -> bool:
        """Fail closed for managed installs unless this user resolves to a formal private scope."""
        if not isinstance(user, dict):
            return False
        synchronizer = getattr(self, "req041_scoped_projection_sync", None)
        status = getattr(self, "req041_migration_status", None)
        scoped_required = isinstance(status, dict) and bool(
            status.get("required") or status.get("scoped_required")
        )
        if synchronizer is None:
            return not scoped_required
        resolver = getattr(self, "_req041_scoped_context_for_user", None)
        if not callable(resolver):
            return False
        try:
            return resolver(user, kind="private", purpose="memory_write") is not None
        except Exception:
            return False

    def _req041_private_memory_managed(self) -> bool:
        if getattr(self, "req041_scoped_projection_sync", None) is not None:
            return True
        status = getattr(self, "req041_migration_status", None)
        return isinstance(status, dict) and bool(
            status.get("required") or status.get("scoped_required")
        )

    def _req041_private_memory_unique_legacy_source(self, user: dict[str, Any]) -> bool:
        person_id = _single_line(user.get("unified_person_id"), 80) if isinstance(user, dict) else ""
        subject = _single_line(
            user.get("identity_subject_id") or user.get("user_id"), 160
        ) if isinstance(user, dict) else ""
        if not person_id or not subject:
            return False
        registry_getter = getattr(self, "_active_unified_person_registry", None)
        registry = registry_getter() if callable(registry_getter) else None
        if registry is None or not registry.matches_person_subject(person_id, subject):
            return False
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        if not isinstance(users, dict):
            return False
        matches = []
        for legacy_key, candidate in users.items():
            if not isinstance(candidate, dict) or candidate.get("unified_person_id") != person_id:
                continue
            candidate_subject = _single_line(
                candidate.get("identity_subject_id") or candidate.get("user_id") or legacy_key, 160
            )
            if candidate_subject and registry.matches_person_subject(person_id, candidate_subject):
                matches.append(candidate)
        return len(matches) == 1 and matches[0] is user

    def _req041_prepare_authoritative_private_memory(self, user: dict[str, Any]) -> int | None:
        if not self._req041_private_memory_write_allowed(user):
            return None
        person_id = _single_line(user.get("unified_person_id"), 80)
        if not person_id or not isinstance(getattr(self, "data", None), dict):
            return None
        try:
            store = AuthoritativePrivateMemoryStore(self.data)
            result = store.read(person_id)
            bootstrapped = False
            if result.get("code") == "not_found":
                seed = (
                    private_memory_content(user)
                    if self._req041_private_memory_unique_legacy_source(user)
                    else {}
                )
                result = store.commit(
                    person_id,
                    seed,
                    expected_revision=0,
                    operation_id=f"req041-private-memory-bootstrap:{person_id}",
                )
                bootstrapped = result.get("ok") is True
            record = result.get("record") if isinstance(result, dict) else None
            if result.get("ok") is not True or not isinstance(record, dict):
                return None
            content = record.get("content")
            if not isinstance(content, dict):
                return None
            apply_private_memory_content(user, content)
            if bootstrapped:
                scheduler = getattr(self, "_schedule_data_save", None)
                if callable(scheduler):
                    scheduler(sections={"users", "_req041_private_memory"})
            return int(record.get("revision") or 0) or None
        except (AuthoritativePrivateMemoryError, TypeError, ValueError) as exc:
            logger.warning(
                "REQ-041 权威私聊记忆准备失败: %s",
                _single_line(exc, 120),
            )
            return None

    def _req041_commit_authoritative_private_memory(
        self,
        user: dict[str, Any],
        *,
        expected_revision: int,
        operation_id: str,
        fields: Any = None,
    ) -> bool:
        person_id = _single_line(user.get("unified_person_id"), 80) if isinstance(user, dict) else ""
        if not person_id or not operation_id or not isinstance(getattr(self, "data", None), dict):
            return False
        try:
            store = AuthoritativePrivateMemoryStore(self.data)
            result = store.commit(
                person_id,
                private_memory_content(user),
                expected_revision=expected_revision,
                operation_id=operation_id,
                fields=fields,
            )
            if result.get("ok") is True:
                return True
            current = store.read(person_id)
            record = current.get("record") if isinstance(current, dict) else None
            if isinstance(record, dict) and isinstance(record.get("content"), dict):
                apply_private_memory_content(user, record["content"])
            logger.warning(
                "REQ-041 权威私聊记忆写入拒绝: code=%s",
                _single_line(result.get("code"), 80),
            )
            return False
        except (AuthoritativePrivateMemoryError, TypeError, ValueError) as exc:
            logger.warning(
                "REQ-041 权威私聊记忆写入失败: %s",
                _single_line(exc, 120),
            )
            return False

    def _req041_private_memory_person_key(self, user_id: str) -> str:
        """Stable per-person serialization key: the unified person when known, else the user row."""
        raw = _single_line(user_id, 160)
        normalizer = getattr(self, "_canonical_private_user_id", None)
        canonical = _single_line(normalizer(raw), 160) if callable(normalizer) else ""
        canonical = canonical or raw
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else None
        user = users.get(canonical) if isinstance(users, dict) else None
        person_id = _single_line(user.get("unified_person_id"), 80) if isinstance(user, dict) else ""
        return person_id or canonical

    def _req041_person_write_lock(self, person_key: str) -> asyncio.Lock:
        """Per-person write lock for the REQ-041 background refresh flows."""
        locks = getattr(self, "_req041_person_write_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._req041_person_write_locks = locks
        key = _single_line(person_key, 80)
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        return lock

    def _req041_record_private_memory_write_failure(
        self,
        user: dict[str, Any],
        *,
        task: str,
        now: float,
    ) -> None:
        """方案 C：权威写入被拒时不留静默丢弃——错误与退避写回权威记录。"""
        memory_revision = self._req041_prepare_authoritative_private_memory(user)
        if memory_revision is None:
            return
        user[f"{task}_last_error"] = "private_memory_write_rejected"
        user[f"{task}_retry_after"] = now + self._user_background_task_retry_delay(task)
        user[f"{task}_running_at"] = 0
        self._req041_commit_authoritative_private_memory(
            user,
            expected_revision=memory_revision,
            operation_id=f"req041-{task}-rejected:{uuid.uuid4().hex}",
            fields=(f"{task}_last_error", f"{task}_retry_after", f"{task}_running_at"),
        )

    def _format_companion_memory_for_prompt(self, user: dict[str, Any], *, style_only: bool = False) -> str:
        memory = user.get("companion_memory")
        lines: list[str] = []
        if not isinstance(memory, dict):
            memory = {}
        llm_profile = memory.get("profile")
        if isinstance(llm_profile, dict):
            if style_only:
                hint_text = _single_line(user.get("last_user_message"), 260)

                def _profile_values(key: str, limit: int = 4) -> list[str]:
                    value = llm_profile.get(key)
                    if isinstance(value, list):
                        return [_single_line(item, 60) for item in value[:limit] if _single_line(item, 60)]
                    text = _single_line(value, 120)
                    return [text] if text else []

                def _weak_relevant(text: str) -> bool:
                    if not hint_text:
                        return False
                    lowered_hint = hint_text.lower()
                    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", text)
                    return any(token and token.lower() in lowered_hint for token in tokens)

                def _with_subject(text: str) -> str:
                    text = _single_line(text, 80)
                    if not text:
                        return ""
                    if text.startswith(("用户", "对方")):
                        return text
                    if text.startswith("别"):
                        return f"对方说过“{text}”"
                    if text.startswith(("不", "别", "讨厌", "害怕", "喜欢", "希望", "想要")):
                        return "对方" + text
                    return text

                style_lines: list[str] = []
                for item in _profile_values("strong_memories", 4):
                    natural = _with_subject(item)
                    if natural:
                        style_lines.append(f"记得{natural}")
                for item in _profile_values("boundaries", 4):
                    natural = _with_subject(item)
                    if natural:
                        style_lines.append(f"别踩这个边界，{natural}")
                for item in _profile_values("speaking_style", 3):
                    style_lines.append(f"回复时顺着一点，{item}")
                weak_candidates = _profile_values("weak_preferences", 4) + _profile_values("interests", 4)
                for item in weak_candidates:
                    if _weak_relevant(item):
                        natural = _with_subject(item)
                        if natural:
                            style_lines.append(f"这轮聊到相关内容时记得{natural}")
                return "\n".join(list(dict.fromkeys(style_lines))) if style_lines else "暂无专门沉淀的用户记忆。"
            profile_fields = (
                ("strong_memories", "强记忆"),
                ("weak_preferences", "弱偏好"),
                ("user_traits", "用户画像"),
                ("interests", "兴趣/偏好"),
                ("boundaries", "边界/雷点"),
                ("relationship_notes", "关系线索"),
                ("speaking_style", "说话习惯"),
            )
            for key, label in profile_fields:
                value = llm_profile.get(key)
                if isinstance(value, list):
                    text = "；".join(_single_line(item, 60) for item in value[:5] if _single_line(item, 60))
                else:
                    text = _single_line(value, 180)
                if text:
                    lines.append(f"{label}：{text}")
        if not style_only:
            items = self._companion_memory_relevant_items(user, hint=user.get("last_user_message") or "", limit=8)
            if isinstance(items, list) and items:
                facts = []
                for item in items[:8]:
                    if not isinstance(item, dict):
                        continue
                    text = _single_line(item.get("text"), 90)
                    if text:
                        facts.append(text)
                if facts:
                    lines.append("近期可记住的话：" + " / ".join(facts))
        if not style_only:
            habit_text = self._format_user_behavior_habits_for_prompt(
                user,
                current_only=True,
                limit=1,
                natural=True,
                hint=user.get("last_user_message") or "",
                time_window_minutes=60,
                require_relevant=True,
            )
            if habit_text:
                lines.append(habit_text)
        if not style_only:
            episode_text = self._format_dialogue_episodes_for_prompt(user, hint=user.get("last_user_message") or "")
            open_loop_text = self._format_open_loops_for_prompt(user, hint=user.get("last_user_message") or "")
            recent_context_parts = [part for part in (episode_text, open_loop_text) if part]
            if recent_context_parts:
                lines.append(
                    "近期共同经历：\n"
                    + "\n".join(recent_context_parts)
                    + "\n使用方式：只在和用户当前消息相关、用户主动回到旧话题，或能一句话自然带过时使用；"
                    "不需要为了兑现旧话题打断当前话题。"
                )
            consequence_text = self._format_action_consequence_hint(user)
            if consequence_text:
                lines.append("最近主动行为闭环：\n" + consequence_text)
        # 人格底线过滤：逐行过滤"主人/大人/主子"类称呼，防止记忆沉淀覆盖人格设定
        safe_lines: list[str] = []
        for line in lines:
            if "\n" in line:
                sub_lines = [s for s in line.split("\n") if s]
                if all(self._private_context_line_is_safe(s) for s in sub_lines):
                    safe_lines.append(line)
            elif self._private_context_line_is_safe(line):
                safe_lines.append(line)
        lines = safe_lines
        return "\n".join(lines) if lines else "暂无专门沉淀的用户记忆。"

    def _dialogue_episode_relevance_score(self, item: dict[str, Any], *, hint: str = "") -> float:
        summary = _single_line(item.get("summary"), 140)
        if not summary:
            return 0.0
        searchable_parts = [
            summary,
            _single_line(item.get("emotional_residue"), 100),
            _single_line(item.get("reusable_topic"), 100),
        ]
        for key in ("user_events", "bot_promises", "avoid_next"):
            value = item.get(key)
            if isinstance(value, list):
                searchable_parts.extend(_single_line(part, 80) for part in value if _single_line(part, 80))
        searchable = " ".join(part for part in searchable_parts if part).lower()
        score = 0.0
        created_ts = _safe_float(item.get("created_ts"), 0)
        if created_ts > 0:
            age_hours = max(0.0, (_now_ts() - created_ts) / 3600)
            if age_hours <= 36:
                score += 2.0
            elif age_hours <= 168:
                score += 1.0
        hint_text = _single_line(hint, 260).lower()
        if hint_text:
            tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9_]{3,24}", hint_text)
            for token in dict.fromkeys(tokens):
                if token and token in searchable:
                    score += 2.5
        return score

    def _select_dialogue_episodes_for_prompt(
        self,
        episodes: list[Any],
        *,
        hint: str = "",
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[int, float, dict[str, Any]]] = []
        seen: set[str] = set()
        total = len(episodes)
        for index, item in enumerate(episodes):
            if not isinstance(item, dict):
                continue
            summary = _single_line(item.get("summary"), 120)
            if not summary:
                continue
            signature = self._memory_fact_signature(summary)
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            score = self._dialogue_episode_relevance_score(item, hint=hint)
            if index >= max(0, total - 1):
                score += 3.0
            elif index >= max(0, total - 3):
                score += 1.0
            candidates.append((index, score, item))
        if not candidates:
            return []
        picked = sorted(candidates, key=lambda part: (part[1], part[0]), reverse=True)[: max(1, limit)]
        return [item for _, _, item in sorted(picked, key=lambda part: part[0])]
