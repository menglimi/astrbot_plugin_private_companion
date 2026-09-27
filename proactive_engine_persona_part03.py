# -*- coding: utf-8 -*-
"""ProactiveEnginePersonaPart03Mixin。

由 tools/split_mixin_domain.py 从 proactive_engine_persona.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 111 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEnginePersonaMixin）。
"""
from __future__ import annotations
from .proactive_engine_persona_shared import Any
from .proactive_engine_persona_shared import PROACTIVE_ROUTE_REGISTRY
from .proactive_engine_persona_shared import _engine_host
from .proactive_engine_persona_shared import _safe_float
from .proactive_engine_persona_shared import _safe_int
from .proactive_engine_persona_shared import _single_line
from .proactive_engine_persona_shared import runtime_persona_setting



class ProactiveEnginePersonaPart03Mixin:
    """ProactiveEnginePersonaPart03Mixin（从 ProactiveEnginePersonaMixin 拆出）。"""


    def _cache_proactive_model_judgement(
        self,
        user: dict[str, Any],
        judgement: dict[str, Any],
        *,
        now: float | None = None,
    ) -> None:
        signature = _single_line(judgement.get("signature"), 80) or self._planned_proactive_model_judge_signature(user)
        user["planned_proactive_model_judge_signature"] = signature
        user["planned_proactive_model_judge_result"] = {
            key: value
            for key, value in judgement.items()
            if key in {"decision", "score", "reason", "hard", "delay_minutes", "reason_field", "action", "topic", "motive"}
        }
        judged_at = _engine_host._now_ts() if now is None else now
        user["planned_proactive_model_judge_at"] = judged_at
        cache = user.get("proactive_persona_judge_cache")
        cache = dict(cache) if isinstance(cache, dict) else {}
        ttl = max(
            5,
            _safe_int(
                runtime_persona_setting(self, "proactive_persona_judge_cache_minutes", 180),
                180,
                5,
                720,
            ),
        ) * 60
        cache = {
            key: value for key, value in cache.items()
            if isinstance(value, dict) and judged_at - _safe_float(value.get("judged_at"), 0) <= ttl
        }
        cache[signature] = {"judged_at": judged_at, "result": dict(user["planned_proactive_model_judge_result"])}
        if len(cache) > 16:
            newest = sorted(cache.items(), key=lambda item: _safe_float(item[1].get("judged_at"), 0), reverse=True)[:16]
            cache = dict(newest)
        user["proactive_persona_judge_cache"] = cache

    def _apply_proactive_model_rewrite(self, user: dict[str, Any], judgement: dict[str, Any]) -> bool:
        changed = False
        new_reason = self._normalize_legacy_proactive_text(judgement.get("reason_field"), limit=40)
        new_action = self._normalize_legacy_proactive_text(judgement.get("action"), limit=40)
        new_topic = _single_line(judgement.get("topic"), 80)
        new_motive = self._normalize_internal_motive_text(_single_line(judgement.get("motive"), 180))
        current_reason = self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40)
        current_action = self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40)
        if new_reason:
            current_route = PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=current_reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
                kind=user.get("planned_proactive_kind"),
            )
            rewritten_route = PROACTIVE_ROUTE_REGISTRY.route_for(
                reason=new_reason,
                source=user.get("planned_proactive_source"),
                semantic_kind=user.get("planned_proactive_semantic_kind"),
            )
            if rewritten_route.key != current_route.key:
                new_reason = ""
        if new_reason and new_reason != current_reason:
            user["planned_proactive_reason"] = new_reason
            changed = True
        if new_action and self._action_is_available(new_action, user) and new_action != current_action:
            user["planned_proactive_action"] = new_action
            changed = True
        if new_topic and new_topic != _single_line(user.get("planned_proactive_topic"), 80):
            user["planned_proactive_topic"] = new_topic
            changed = True
        if new_motive and new_motive != _single_line(user.get("planned_proactive_motive"), 180):
            user["planned_proactive_motive"] = new_motive
            changed = True
        if changed and self._private_user_role(user) == "friend":
            sanitized = self._sanitize_friend_proactive_plan_fields(
                user,
                reason=self._normalize_legacy_proactive_text(user.get("planned_proactive_reason"), limit=40) or "check_in",
                action=self._normalize_legacy_proactive_text(user.get("planned_proactive_action"), limit=40) or "message",
                topic=_single_line(user.get("planned_proactive_topic"), 80),
                motive=_single_line(user.get("planned_proactive_motive"), 180),
            )
            user["planned_proactive_reason"] = sanitized["reason"]
            user["planned_proactive_action"] = sanitized["action"]
            user["planned_proactive_topic"] = sanitized["topic"]
            user["planned_proactive_motive"] = sanitized["motive"]
        if changed:
            route_store = getattr(self, "_store_planned_proactive_route_fields", None)
            if callable(route_store):
                route_store(
                    user,
                    {
                        "source": user.get("planned_proactive_source"),
                        "reason": user.get("planned_proactive_reason"),
                        "action": user.get("planned_proactive_action"),
                        "topic": user.get("planned_proactive_topic"),
                        "motive": user.get("planned_proactive_motive"),
                        "origin_event_id": user.get("planned_proactive_origin_event_id"),
                    },
                )
        return changed

    def _persona_action_profile(self) -> dict[str, bool]:
        text = str(self._get_default_persona_prompt() or "")
        playful_markers = ("恶作剧", "小恶魔", "腹黑", "俏皮", "捉弄", "欺负", "调皮")
        clingy_markers = ("依赖", "依恋", "特殊的情感", "知心朋友", "关心", "体贴", "想念", "共犯")
        observant_markers = ("看透", "观察", "温柔", "安静", "留意", "敏锐")
        visual_markers = ("自拍", "照片", "景色", "表情包", "外观", "外形", "穿搭", "发型", "发饰")
        voice_markers = ("悄悄说", "口语化", "抽空回复", "亲切感", "温柔", "顺从")
        return {
            "playful": any(marker in text for marker in playful_markers),
            "clingy": any(marker in text for marker in clingy_markers),
            "observant": any(marker in text for marker in observant_markers),
            "visual": any(marker in text for marker in visual_markers),
            "voicey": any(marker in text for marker in voice_markers),
        }
