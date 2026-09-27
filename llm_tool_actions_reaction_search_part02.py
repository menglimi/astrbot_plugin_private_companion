# -*- coding: utf-8 -*-
"""LlmToolActionsReactionSearchPart02Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_search.py 机械抽取（5 个方法 + 0 个模块级名字 + 0 个类级赋值 / 177 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionSearchMixin）。
"""
from __future__ import annotations

import json
import os
import time
from .helpers import _now_ts, _path_text, _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .reaction_expression import (
    classify_reaction_expression_feedback,
    ensure_reaction_expression_state,
    record_reaction_expression_feedback,
    sync_reaction_expression_auto_preference,
)
from typing import Any



class LlmToolActionsReactionSearchPart02Mixin:
    """LlmToolActionsReactionSearchPart02Mixin（从 LlmToolActionsReactionSearchMixin 拆出）。"""


    def _reaction_expression_lookup_cache_get(
        self,
        key: tuple[int, str, str, bool, str, str],
    ) -> dict[str, Any] | None:
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        for cached_key, entry in list(cache.items()):
            if not isinstance(entry, dict) or now > _safe_float(entry.get("expires_at"), 0.0):
                cache.pop(cached_key, None)
        entry = cache.get(key)
        if not isinstance(entry, dict):
            return None
        lookup = entry.get("lookup")
        if not isinstance(lookup, dict):
            cache.pop(key, None)
            return None
        if lookup.get("success"):
            cached_path = _path_text(lookup.get("path"), 1000)
            if not cached_path or not os.path.isfile(cached_path):
                cache.pop(key, None)
                return None
        return dict(lookup)

    def _reaction_expression_lookup_cache_put(
        self,
        key: tuple[int, str, str, bool, str, str],
        lookup: dict[str, Any],
    ) -> None:
        if not isinstance(lookup, dict):
            return
        status = _single_line(lookup.get("status"), 40).lower()
        success = bool(lookup.get("success"))
        if success:
            image_path = _path_text(lookup.get("path"), 1000)
            if not image_path or not os.path.isfile(image_path):
                return
            ttl_seconds = 120.0
        elif status in {"not_found", "empty_library"}:
            ttl_seconds = 30.0
        else:
            return
        cache = getattr(self, "_reaction_expression_lookup_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_reaction_expression_lookup_cache", cache)
        now = time.monotonic()
        cache[key] = {
            "lookup": dict(lookup),
            "created_at": now,
            "expires_at": now + ttl_seconds,
        }
        if len(cache) > 48:
            oldest = sorted(
                cache.items(),
                key=lambda item: _safe_float(item[1].get("created_at"), 0.0)
                if isinstance(item[1], dict)
                else 0.0,
            )
            for cached_key, _entry in oldest[: len(cache) - 48]:
                cache.pop(cached_key, None)

    def _reaction_expression_lookup_context(
        self,
        user: dict[str, Any],
        intent: dict[str, Any],
        *,
        profile_snapshot: dict[str, Any] | None = None,
    ) -> str:
        parts = [
            "实验性表情表达：仅在候选自然贴合时选择，不合适时允许不返回图片。",
            f"沟通用途：{_single_line(intent.get('purpose'), 120)}"
            if intent.get("purpose")
            else "",
            f"表达情绪：{_single_line(intent.get('emotion'), 80)}"
            if intent.get("emotion")
            else "",
            f"表达强度：{_safe_int(intent.get('intensity'), 0, 0, 5)}/5",
            f"当前语境：{_single_line(intent.get('context'), 500)}"
            if intent.get("context")
            else "",
        ]
        candidates = intent.get("candidate_queries")
        if isinstance(candidates, list) and candidates:
            parts.append(f"候选检索表达：{'；'.join(_single_line(item, 100) for item in candidates)}")
        intent_profile = (
            profile_snapshot
            if isinstance(profile_snapshot, dict) and profile_snapshot
            else user.get("intent_profile")
        )
        if isinstance(intent_profile, dict) and intent_profile:
            parts.append(
                "近期用户意图："
                + _single_line(json.dumps(intent_profile, ensure_ascii=False), 260)
            )
        expression_builder = getattr(self, "_build_expression_decision_for_user", None)
        if callable(expression_builder):
            try:
                decision = expression_builder(
                    user,
                    message_intent={"requested_content_tier": "normal"},
                    passive_reengagement=True,
                )
                expression = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
                parts.append(
                    "统一表达边界："
                    f"档位={_single_line(expression.get('expression_band'), 24) or 'relaxed'}，"
                    f"语气={_single_line(expression.get('tone'), 24) or 'steady'}，"
                    f"追问={'允许' if expression.get('followup') else '关闭'}，"
                    f"内容尺度={_single_line(expression.get('content_tier'), 16) or 'normal'}"
                )
            except Exception:
                pass
        preference = ensure_reaction_expression_state(user).get("preference")
        if isinstance(preference, dict):
            score = _safe_int(preference.get("score"), 0, -20, 20)
            if score:
                parts.append(f"用户对近期表情表达的轻量偏好分：{score}")
        return _single_line("；".join(part for part in parts if part), 1000)

    def _record_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        signal: str,
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        return record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=_now_ts(),
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )

    def _apply_reaction_expression_feedback(
        self,
        user: dict[str, Any],
        text: str,
        *,
        scope_key: str = "",
    ) -> dict[str, Any]:
        state = ensure_reaction_expression_state(user)
        now = _now_ts()
        preference_change = sync_reaction_expression_auto_preference(
            state, text, now=now, scope_key=scope_key
        )
        signal = classify_reaction_expression_feedback(
            state,
            text,
            now=now,
            scope_key=scope_key,
        )
        if not signal:
            if preference_change:
                return {
                    "auto_preference": preference_change,
                    "score": _safe_int(
                        (state.get("preference") or {}).get("score"),
                        0,
                        -20,
                        20,
                    ),
                }
            return {}
        result = record_reaction_expression_feedback(
            state,
            signal,
            text,
            now=now,
            event_limit=max(8, _safe_int(runtime_persona_setting(self, 'reaction_expression_candidate_limit', 6), 6, 1, 16) * 2),
            scope_key=scope_key,
        )
        if preference_change:
            result["auto_preference"] = preference_change
        return result
