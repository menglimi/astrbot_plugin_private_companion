# -*- coding: utf-8 -*-
"""LlmToolActionsReactionCorePart04Mixin。

由 tools/split_mixin_domain.py 从 llm_tool_actions_reaction_core.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 118 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 LlmToolActionsReactionCoreMixin）。
"""
from __future__ import annotations

from .llm_tool_actions_reaction_core_shared import _REACTION_LOG_TRIGGER_MODES
from .llm_tool_actions_reaction_core_shared import Any
from .llm_tool_actions_reaction_core_shared import _now_ts
from .llm_tool_actions_reaction_core_shared import _safe_float
from .llm_tool_actions_reaction_core_shared import _safe_int
from .llm_tool_actions_reaction_core_shared import _single_line



class LlmToolActionsReactionCorePart04Mixin:
    """LlmToolActionsReactionCorePart04Mixin（从 LlmToolActionsReactionCoreMixin 拆出）。"""


    def _reaction_expression_skip_result(
        self,
        reason: str,
        *,
        event: Any = None,
        stage: str = "decision",
        scope: str = "",
        message: str = "本轮不使用表情表达，继续自然文字回复即可",
        **extra: Any,
    ) -> dict[str, Any]:
        self._note_reaction_expression_runtime(skipped=1, last_reason=reason)
        payload: dict[str, Any] = {
            "status": "skipped",
            "success": True,
            "found": False,
            "sent": False,
            "experimental": True,
            "decision": "skip",
            "skip_reason": _single_line(reason, 80),
            "message": _single_line(message, 240),
            "must_not_claim_sent": True,
            "final_response_instruction": "无需向用户解释跳过原因，按原语境继续自然文字回复。",
        }
        payload.update(extra)
        self._log_reaction_expression_event(
            event,
            stage=stage,
            decision="skip",
            reason=reason,
            scope=scope or (self._reaction_expression_scope(event) if event is not None else ""),
            found=bool(payload.get("found")),
            sent=False,
            image_id=payload.get("image_id"),
            confidence=payload.get("confidence") if "confidence" in payload else None,
            cache_hit=payload.get("cache_hit") if "cache_hit" in payload else None,
            latency_ms=(
                payload.get("lookup_latency_ms")
                if "lookup_latency_ms" in payload
                else None
            ),
            delivery=payload.get("delivery"),
        )
        return payload

    def _note_reaction_expression_runtime(
        self,
        *,
        attempts: int = 0,
        offers: int = 0,
        model_omissions: int = 0,
        local_fallbacks: int = 0,
        lookups: int = 0,
        cache_hits: int = 0,
        sent: int = 0,
        skipped: int = 0,
        last_reason: str = "",
        trigger_mode: Any = "",
        latency_ms: float | None = None,
        lookup_elapsed_ms: float = 0.0,
    ) -> None:
        runtime = getattr(self, "_reaction_expression_runtime", None)
        if not isinstance(runtime, dict):
            runtime = {}
            setattr(self, "_reaction_expression_runtime", runtime)
        for key, increment in (
            ("attempts", attempts),
            ("offers", offers),
            ("model_omissions", model_omissions),
            ("local_fallbacks", local_fallbacks),
            ("lookups", lookups),
            ("cache_hits", cache_hits),
            ("sent", sent),
            ("skipped", skipped),
        ):
            if increment:
                runtime[key] = max(0, _safe_int(runtime.get(key), 0)) + max(
                    0, _safe_int(increment, 0)
                )
        if lookup_elapsed_ms > 0:
            runtime["total_lookup_ms"] = round(
                max(0.0, _safe_float(runtime.get("total_lookup_ms"), 0.0))
                + max(0.0, _safe_float(lookup_elapsed_ms, 0.0)),
                2,
            )
        if latency_ms is not None:
            runtime["last_latency_ms"] = round(
                max(0.0, _safe_float(latency_ms, 0.0)), 2
            )
        if last_reason:
            runtime["last_reason"] = _single_line(last_reason, 120)
        normalized_trigger_mode = _single_line(trigger_mode, 40).casefold()
        if normalized_trigger_mode in _REACTION_LOG_TRIGGER_MODES:
            trigger_counts = runtime.get("trigger_modes")
            if not isinstance(trigger_counts, dict):
                trigger_counts = {}
                runtime["trigger_modes"] = trigger_counts
            trigger_counts[normalized_trigger_mode] = (
                max(0, _safe_int(trigger_counts.get(normalized_trigger_mode), 0)) + 1
            )
        runtime["last_at"] = _now_ts()

    def _persist_reaction_expression_state(
        self,
        *,
        sections: set[str] | None = None,
    ) -> None:
        scheduler = getattr(self, "_schedule_data_save", None)
        if callable(scheduler):
            try:
                scheduler(sections=sections)
                return
            except Exception:
                pass
        saver = getattr(self, "_save_data_sync", None)
        if callable(saver):
            requested = sections or {"users", "reaction_expression_group_states"}
            try:
                saver(sections=requested)
            except TypeError:
                saver()
