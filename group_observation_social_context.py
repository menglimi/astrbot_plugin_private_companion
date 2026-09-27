# -*- coding: utf-8 -*-
"""GroupObservationSocialContextMixin。

由 tools/split_mixin_domain.py 从 group_observation.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 329 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GroupObservationMixin）。
"""
from __future__ import annotations

import re
from .conversation_prompt_section import PromptSection, prompt_list, prompt_section
from .domains.social.group_moments import (
    extract_group_moment_candidates,
    extract_moment_portrait_candidates,
    select_group_moments_for_prompt,
    settle_group_moments,
)
from .domains.social.group_mood import project_group_mood_prompt_facts, settle_group_mood
from .domains.social.joke_boundary import joke_guard_suggestion, settle_joke_boundary
from .domains.social.roleplay_strength import project_roleplay_strength
from .group_observation_shared import _persona_value, logger
from .helpers import _now_ts, _safe_float, _single_line
from datetime import datetime
from typing import Any



class GroupObservationSocialContextMixin:
    """GroupObservationSocialContextMixin（从 GroupObservationMixin 拆出）。"""


    def _update_group_atmosphere(self, group: dict[str, Any]) -> None:
        recent = self._filtered_group_recent_messages(group)
        now = _now_ts()
        previous = group.get("atmosphere") if isinstance(group.get("atmosphere"), dict) else {}
        reset_at = _safe_float(previous.get("reset_at"), 0)
        window_start = now - 12 * 60
        if window_start < reset_at <= now:
            window_start = reset_at
        window = [
            item
            for item in recent
            if isinstance(item, dict) and window_start < _safe_float(item.get("ts"), 0) <= now
        ]
        texts = [str(item.get("text") or "") for item in window]
        joined = "\n".join(texts)
        active_speakers = len({str(item.get("sender_id") or "") for item in window if isinstance(item, dict)})
        pace = "安静"
        if len(window) >= 18 or active_speakers >= 6:
            pace = "热闹"
        elif len(window) >= 6:
            pace = "有来有回"
        mood = "平稳"
        if re.search(r"(哈哈|笑死|草|乐|绷|hhh)", joined, re.IGNORECASE):
            mood = "玩笑"
        strong_tension_hits = re.findall(r"(别吵|吵架|争吵|闭嘴|骂人|生气|急眼|烦死)", joined)
        soft_tension_hits = re.findall(r"(烦|累|难受|着急)", joined)
        if strong_tension_hits or (active_speakers >= 2 and len(soft_tension_hits) >= 2):
            mood = "紧绷"
        if re.search(r"(求助|怎么|为什么|报错|帮|救命)", joined):
            mood = "求助"
        atmosphere = {
            "pace": pace,
            "mood": mood,
            "active_speakers": active_speakers,
            "recent_count": len(window),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        if reset_at > now - 12 * 60:
            atmosphere["reset_at"] = reset_at
        group["atmosphere"] = atmosphere

    def _update_group_social_context(self, group: dict[str, Any], *, now: float | None = None) -> None:
        """集成入口：氛围感知 / 名场面 / 接梗边界（受分项开关控制，默认关闭）。"""
        now = _now_ts() if now is None else max(0.0, _safe_float(now, 0))
        if _persona_value(self, "enable_group_mood_detection", False):
            try:
                self._update_group_mood(group, now=now)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 群聊氛围感知更新失败: %s", _single_line(exc, 120))
        if not (_persona_value(self, "enable_group_moments", False) and _persona_value(self, "enable_group_moment_portrait", False)):
            group.pop("moment_portrait_candidates", None)
        if _persona_value(self, "enable_group_moments", False):
            try:
                self._update_group_moments(group, now=now)
                if _persona_value(self, "enable_group_moment_portrait", False):
                    self._update_group_moment_portrait_candidates(group, now=now)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 群聊名场面更新失败: %s", _single_line(exc, 120))
        if _persona_value(self, "enable_group_joke_guard", False):
            try:
                self._update_group_joke_boundary(group, now=now)
            except Exception as exc:
                logger.debug("[PrivateCompanion] 群聊接梗边界更新失败: %s", _single_line(exc, 120))

    def _update_group_mood(self, group: dict[str, Any], *, now: float) -> None:
        messages = self._filtered_group_recent_messages(group)[-16:]
        group["social_mood"] = settle_group_mood(
            group.get("social_mood"),
            messages=messages,
            now=now,
        )

    def _update_group_moments(self, group: dict[str, Any], *, now: float) -> None:
        messages = self._filtered_group_recent_messages(group)[-24:]
        candidates = extract_group_moment_candidates(messages, now=now)
        if not candidates:
            return
        group["social_moments"] = settle_group_moments(
            group.get("social_moments"),
            candidates=candidates,
            now=now,
        )

    def _update_group_moment_portrait_candidates(self, group: dict[str, Any], *, now: float) -> None:
        """Keep provisional interaction evidence inside its source group."""
        moments = group.get("social_moments")
        candidates = extract_moment_portrait_candidates(moments, now=now)
        group["moment_portrait_candidates"] = candidates

    def _update_group_joke_boundary(self, group: dict[str, Any], *, now: float) -> None:
        messages = self._filtered_group_recent_messages(group)[-16:]
        group["social_joke_boundary"] = settle_joke_boundary(
            group.get("social_joke_boundary"),
            messages=messages,
            now=now,
        )

    @staticmethod
    def _group_social_mood_prompt_section(
        mood: dict[str, Any],
        *,
        now: float,
        max_detail: int = 3,
    ) -> PromptSection | None:
        facts = project_group_mood_prompt_facts(
            mood,
            now=now,
            max_detail=max_detail,
        )
        if not facts:
            return None
        parts: list[str] = []
        top_mood = str(facts.get("top_mood") or "")
        if top_mood and top_mood != "dead_silence":
            parts.append(f"当前氛围以「{facts.get('top_mood_label') or top_mood}」为主")
        details = facts.get("detail_moods")
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                label = _single_line(detail.get("label"), 24)
                if label:
                    parts.append(f"含「{label}」成分")
        tension = _safe_float(facts.get("social_tension"), 0)
        tension_level = str(facts.get("tension_level") or "")
        if tension_level == "high":
            parts.append(f"群内社交张力较高（{int(tension)}）")
        elif tension_level == "low":
            parts.append("群内气氛轻松无火药味")
        if not parts:
            parts.append("群聊最近无明显情绪信号，保持自然回应")
        return prompt_section(
            key="group.social_mood",
            title="群聊氛围",
            source="group_observation",
            content="；".join(parts),
        )

    @staticmethod
    def _group_social_moments_prompt_section(
        moments: dict[str, Any],
        *,
        now: float,
        limit: int = 3,
    ) -> PromptSection | None:
        selected = select_group_moments_for_prompt(
            moments,
            now=now,
            limit=limit,
        )
        if not selected:
            return None
        lines = [
            f"{str(item.get('sender') or '群友')}：{str(item.get('text') or '')}"
            for item in selected
            if isinstance(item, dict) and str(item.get("text") or "")
        ]
        if not lines:
            return None
        return prompt_section(
            key="group.social_moments",
            title="群聊名场面（可选回忆）",
            source="group_observation",
            content=prompt_list(
                lines,
                tag="moments",
                item_tag="moment",
                separator="\n",
            ),
        )

    @staticmethod
    def _group_roleplay_strength_prompt_section(
        mood: dict[str, Any],
        *,
        expression_band: str = "relaxed",
        now: float,
    ) -> PromptSection | None:
        projection = project_roleplay_strength(
            mood,
            expression_band=expression_band,
            now=now,
        )
        voice_by_band = {
            "playful_high": "群聊正处在玩闹气氛，可以适度夸张、接梗、起哄，让回复更有参与感；不要刻意抢话或攻击谁",
            "playful_moderate": "群聊气氛轻松，可以带一点俏皮和接梗，但保持自然，不强行玩梗",
            "reserved": "群聊气氛偏正或偏冷，保持克制、回应分寸，不开玩笑",
            "minimal": "群聊气氛紧张或沉默，只做必要回应，压低表达强度，不制造冲突",
        }
        content = voice_by_band.get(str(projection.get("strength_band") or ""), "")
        if not content:
            return None
        return prompt_section(
            key="group.roleplay_strength",
            title="扮演强度",
            source="group_observation",
            content=content,
        )

    @staticmethod
    def _group_joke_boundary_prompt_section(
        boundary: dict[str, Any],
        *,
        member_id: str,
    ) -> PromptSection | None:
        guard = joke_guard_suggestion(boundary, member_id=member_id)
        reason_by_code = {
            "repeated_serious_objection_or_recall": "该成员已多次严肃反对或撤回玩笑，避免再向其开玩笑",
            "low_joke_acceptance": "该成员对玩笑的接受度偏低，开玩笑前先确认",
        }
        content = reason_by_code.get(str(guard.get("reason_code") or ""), "")
        if not content:
            return None
        return prompt_section(
            key="group.joke_boundary",
            title="玩笑边界提醒",
            source="group_observation",
            content=content,
        )

    def _append_group_social_context_sections(
        self,
        group: dict[str, Any],
        sections: list[PromptSection],
        *,
        sender_id: str = "",
        now: float | None = None,
    ) -> None:
        """被动管线注入：氛围摘要 / 名场面 / 扮演强度 / 玩笑边界提醒。

        这些段只作为当下群聊语感的软参考，统一在段首附一句整体引导，
        明确它们不覆盖人物画像与长期记忆，仅调节语气与接梗分寸。
        """
        now = _now_ts() if now is None else max(0.0, _safe_float(now, 0))
        start = len(sections)
        mood = group.get("social_mood") if isinstance(group.get("social_mood"), dict) else None
        if mood and _persona_value(self, "enable_group_mood_detection", False):
            mood_section = self._group_social_mood_prompt_section(mood, now=now)
            if mood_section is not None:
                sections.append(mood_section)
        moments = group.get("social_moments") if isinstance(group.get("social_moments"), dict) else None
        if moments and _persona_value(self, "enable_group_moments", False):
            moments_section = self._group_social_moments_prompt_section(
                moments,
                now=now,
                limit=3,
            )
            if moments_section is not None:
                sections.append(moments_section)
            if sender_id and _persona_value(self, "enable_group_moment_portrait", False):
                candidates = extract_moment_portrait_candidates(moments, now=now)
                claims = [
                    item["claim"] for item in candidates
                    if item["sender"] == str(sender_id)
                ]
                if claims:
                    sections.append(prompt_section(
                        key="group.moment_interaction_evidence",
                        title="当前群成员互动线索",
                        source="group_observation",
                        content="\n".join(claims) + "\n这些只是本群单次互动线索，结合原话和当前语境理解；不能据此推定长期偏好、身份或在其他会话中的边界。",
                    ))
        if _persona_value(self, "enable_group_roleplay_strength", False) and mood:
            expression_band = "relaxed"
            users = self.data.get("users", {}) if isinstance(getattr(self, "data", None), dict) else {}
            current_user = users.get(sender_id) if isinstance(users, dict) else None
            if isinstance(current_user, dict):
                interaction = current_user.get("current_interaction")
                if isinstance(interaction, dict):
                    expression_band = str(
                        interaction.get("expression_band")
                        or interaction.get("base_band")
                        or expression_band
                    )
            roleplay_section = self._group_roleplay_strength_prompt_section(
                mood,
                expression_band=expression_band,
                now=now,
            )
            if roleplay_section is not None:
                sections.append(roleplay_section)
        if _persona_value(self, "enable_group_joke_guard", False):
            boundary = group.get("social_joke_boundary") if isinstance(group.get("social_joke_boundary"), dict) else None
            if boundary and sender_id:
                joke_section = self._group_joke_boundary_prompt_section(
                    boundary,
                    member_id=sender_id,
                )
                if joke_section is not None:
                    sections.append(joke_section)
        if len(sections) > start:
            sections.insert(
                start,
                prompt_section(
                    key="group.social_soft_reference",
                    title="群聊社交语境（整体软参考）",
                    source="group_observation",
                    content=(
                        "以下氛围、名场面、扮演强度与玩笑边界只描述群聊当下的气氛和共同回忆，均为软参考，"
                        "用于调节表达语气与接梗分寸；它们不高于你对群友的持久画像与长期记忆，也不单独压制回复。"
                    ),
                ),
            )

    async def _note_group_joke_boundary_recall(self, group_id: str, sender_id: str) -> bool:
        """群聊撤回事件 → 接梗边界 recall 信号（受 enable_group_joke_guard 控制）。"""
        if not group_id or not sender_id:
            return False
        if not _persona_value(self, "enable_group_joke_guard", False):
            return False
        getter = getattr(self, "_get_group", None)
        saver = getattr(self, "_save_data_sync", None)
        if not callable(getter) or not callable(saver):
            return False
        try:
            lock = getattr(self, "_data_lock", None)
            if lock is not None and hasattr(lock, "__aenter__"):
                async with lock:
                    return self._settle_group_joke_boundary_recall(getter(group_id), sender_id, saver)
            return self._settle_group_joke_boundary_recall(getter(group_id), sender_id, saver)
        except Exception as exc:
            logger.debug("[PrivateCompanion] 撤回边界信号更新失败: %s", _single_line(exc, 120))
            return False

    def _settle_group_joke_boundary_recall(self, group: Any, sender_id: str, saver: Any) -> bool:
        if not isinstance(group, dict):
            return False
        now = _now_ts()
        group["social_joke_boundary"] = settle_joke_boundary(
            group.get("social_joke_boundary"),
            messages=[
                {
                    "sender_id": sender_id,
                    "kind": "recall",
                    "signal_id": f"recall:{sender_id}:{now:.6f}",
                }
            ],
            now=now,
        )
        saver(sections={"groups"})
        return True
