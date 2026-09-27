# -*- coding: utf-8 -*-
"""动作选择域。

由 tools/split_mixin_domain.py 从 proactive_engine.py 机械抽取（11 个方法 + 0 个模块级名字 + 0 个类级赋值 / 299 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 ProactiveEngineMixin）。
"""
from __future__ import annotations
from .proactive_engine_shared import _engine_host

import random
import re
from .helpers import _safe_float, _safe_int, _today_key
from .persona_config import runtime_persona_setting
from types import SimpleNamespace
from typing import Any



class ProactiveEngineActionMixin:
    """动作选择域（从 ProactiveEngineMixin 拆出）。"""


    def _note_action_affinity_sent(self, user: dict[str, Any], action: str) -> None:
        raw = user.setdefault("action_reply_affinity", {})
        if not isinstance(raw, dict):
            raw = {}
            user["action_reply_affinity"] = raw
        today = _today_key()
        for part in [item.strip() for item in str(action or "message").split("+") if item.strip()]:
            if part == "message":
                continue
            stats = raw.get(part)
            if not isinstance(stats, dict):
                legacy_replied = _safe_int(stats, 0, 0)
                stats = {"sent": legacy_replied, "replied": legacy_replied}
                raw[part] = stats
            stats["sent"] = _safe_int(stats.get("sent"), 0, 0) + 1
            if part == "photo_text":
                if self._private_user_role(user) == "friend":
                    continue
                photo_sent_day = str(user.get("photo_sent_day") or "")
                if photo_sent_day != today:
                    user["photo_sent_day"] = today
                    user["photo_sent_today"] = 1
                else:
                    user["photo_sent_today"] = _safe_int(user.get("photo_sent_today"), 0) + 1

    def _note_action_affinity_reply_feedback(self, user: dict[str, Any], action: str) -> None:
        raw = user.setdefault("action_reply_affinity", {})
        if not isinstance(raw, dict):
            raw = {}
            user["action_reply_affinity"] = raw
        for part in [item.strip() for item in str(action or "message").split("+") if item.strip()]:
            if part == "message":
                continue
            stats = raw.get(part)
            if not isinstance(stats, dict):
                legacy_replied = _safe_int(stats, 0, 0)
                # A reply can arrive after upgrading from the old integer-only
                # format, while the matching send was never counted as sent.
                stats = {"sent": legacy_replied + 1, "replied": legacy_replied}
                raw[part] = stats
            stats["replied"] = _safe_int(stats.get("replied"), 0, 0) + 1
            stats["sent"] = max(
                _safe_int(stats.get("sent"), 0, 0),
                _safe_int(stats.get("replied"), 0, 0),
            )

    def _parse_action_list(self, raw: Any) -> set[str]:
        if raw is None:
            return set()
        if isinstance(raw, str):
            parts = re.split(r"[,\s,、;；]+", raw)
        elif isinstance(raw, list):
            parts = raw
        else:
            parts = []
        return {str(part).strip() for part in parts if str(part).strip()}

    def _action_affinity_bias(self, user: dict[str, Any] | None = None) -> dict[str, float]:
        base = {"screen_peek": 0.0, "photo_text": 0.0, "poke": 0.0, "voice": 0.0}
        if not isinstance(user, dict):
            return base
        raw = user.get("action_reply_affinity")
        if not isinstance(raw, dict):
            return base
        for action in base:
            stats = raw.get(action)
            if not isinstance(stats, dict):
                continue
            sent = _safe_int(stats.get("sent"), 0, 0)
            replied = _safe_int(stats.get("replied"), 0, 0)
            if sent <= 0:
                continue
            rate = replied / max(1, sent)
            base[action] = max(-0.08, min(0.28, (rate - 0.35) * 0.55))
        return base

    def _poke_available(self) -> bool:
        if not runtime_persona_setting(self, "enable_poke_action", False):
            return False
        if self._resolve_aiocqhttp_client() is None:
            return False
        try:
            from data.plugins.astrbot_plugin_pokepro.core.send_poke import PokeSender  # noqa: F401
            return True
        except Exception:
            try:
                from astrbot_plugin_pokepro.core.send_poke import PokeSender  # noqa: F401
                return True
            except Exception:
                return False

    def _voice_available(self, user: dict[str, Any] | None = None) -> bool:
        if not runtime_persona_setting(self, "enable_voice_action", False):
            return False
        target = ""
        if isinstance(user, dict):
            target = str(user.get("umo") or "").strip()
        if not target:
            return False
        try:
            config = self.context.get_config(target)
        except Exception:
            try:
                config = self.context.get_config()
            except Exception:
                return False
        provider_settings = dict(config.get("provider_tts_settings", {}) or {})
        astrbot_provider = None
        try:
            astrbot_provider = self.context.get_using_tts_provider(target)
        except Exception:
            astrbot_provider = None
        resolver = getattr(self, "_resolve_tts_synthesis_provider", None)
        if callable(resolver):
            try:
                resolved_provider = resolver(SimpleNamespace(unified_msg_origin=target), astrbot_provider)
            except Exception:
                resolved_provider = astrbot_provider
        else:
            resolved_provider = astrbot_provider
        if resolved_provider is None:
            return False
        if resolved_provider is astrbot_provider:
            return bool(provider_settings.get("enable", False))
        return True

    def _action_is_available(self, action: str, user: dict[str, Any] | None = None) -> bool:
        normalized = str(action or "message").strip()
        if not normalized or normalized == "message":
            return True
        if self._friend_sensitive_proactive_action(normalized) and isinstance(user, dict) and self._private_user_role(user) == "friend":
            return False
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        if not parts:
            return True
        screen_quota_exempt = bool(isinstance(user, dict) and user.get("planned_proactive_quota_exempt"))
        user_umo = str((user or {}).get("umo") or "") if isinstance(user, dict) else ""
        platform_supports = getattr(self, "_platform_supports", None)
        for part in parts:
            capability = {"poke": "poke", "photo_text": "image", "voice": "voice"}.get(part)
            if capability and callable(platform_supports) and not platform_supports(capability, umo=user_umo):
                return False
            if part == "screen_peek" and not self._screen_glance_available(user, ignore_daily_limit=screen_quota_exempt):
                return False
            if part == "photo_text" and not self._photo_text_available(user):
                return False
            if part == "poke" and (
                not self._poke_available()
                or self._effective_user_poke_daily_limit(user) <= 0
                or self._poke_action_cooldown_remaining(user) > 0
            ):
                return False
            if part == "voice" and not self._voice_available(user):
                return False
            if part.startswith("external:"):
                external_name = self._normalize_external_ability_name(part.split(":", 1)[1])
                available_external = {
                    self._normalize_external_ability_name(item.get("name"))
                    for item in self._available_external_proactive_abilities(user)
                    if isinstance(item, dict)
                }
                if external_name not in available_external:
                    return False
        return True

    def _fallback_action_for_unavailable(self, action: str, user: dict[str, Any] | None = None) -> str:
        normalized = str(action or "message").strip() or "message"
        if self._action_is_available(normalized, user):
            return normalized
        parts = [part.strip() for part in normalized.split("+") if part.strip()]
        available_parts = [part for part in parts if self._action_is_available(part, user)]
        if not available_parts:
            return "message"
        return "+".join(available_parts)

    def _choose_action_for_reason(
        self,
        reason: str,
        user: dict[str, Any] | None = None,
        motive: str = "",
    ) -> str:
        weather = self._weather_summary_text(self.data.get("daily_weather", {}))
        state = self.data.get("daily_state", {})
        energy = _safe_int(state.get("energy") if isinstance(state, dict) else 70, 70, 0, 100)
        action_profile = self._persona_action_profile()
        motive_bias = self._motive_action_bias(motive)
        affinity_bias = self._action_affinity_bias(user)
        busy_voice_context = self._busy_proactive_voice_context()
        busy_schedule = bool(busy_voice_context.get("busy"))
        current_item = self._proactive_current_agenda_item()
        current_item_text = self._format_plan_item_for_prompt(current_item)

        weighted: list[tuple[str, float]] = [("message", 0.82)]
        if self._screen_glance_available(user) and reason in {"check_in", "quiet_care", "state_share", "background_schedule"}:
            weight = 0.9 + (0.45 if action_profile["observant"] else 0.0) + motive_bias["screen_peek"] + affinity_bias["screen_peek"]
            if energy < 50:
                weight += 0.12
            weighted.append(("screen_peek", weight))
        if (
            self._photo_text_available(user)
            and reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            and self._strong_photo_share_intent(motive, user.get("planned_proactive_topic") if isinstance(user, dict) else "")
        ):
            return "photo_text"
        photo_probability = self._proactive_photo_text_trigger_probability(
            reason,
            motive,
            user.get("planned_proactive_topic") if isinstance(user, dict) else "",
            weather,
            current_item_text,
            user=user,
        )
        if self._photo_text_available(user) and photo_probability > 0 and _engine_host.random.random() < photo_probability:
            return "photo_text"
        visual_hint = any(token in motive for token in self._visual_share_tokens())
        if self._photo_text_available(user) and (
            reason in {"activity_share", "diary_share", "background_schedule", "noon_greeting", "evening_greeting"}
            or photo_probability > 0
        ):
            weight = 0.38 + (0.18 if action_profile["visual"] else 0.0) + motive_bias["photo_text"] * 0.65 + affinity_bias["photo_text"]
            if any(token in weather for token in ("晴", "阳光", "多云", "晚霞", "雨", "阵雨", "小雨")):
                weight += 0.04
            if visual_hint:
                weight += 0.14
            if reason in {"activity_share", "diary_share"}:
                weight += 0.05
            if reason in {"check_in", "quiet_care", "state_share"}:
                weight *= 0.45
            weighted.append(("photo_text", weight))
        if self._poke_available() and self._effective_user_poke_daily_limit(user) > 0 and self._poke_action_cooldown_remaining(user) <= 0 and reason in {"check_in", "quiet_care", "state_share", "important_date_share", "morning_greeting", "evening_greeting"}:
            weight = 0.38 + motive_bias["poke"] + affinity_bias["poke"]
            if action_profile["playful"]:
                weight += 0.22
            if action_profile["clingy"]:
                weight += 0.12
            weighted.append(("poke", weight))
        if self._voice_available(user) and (
            reason in {"state_share", "diary_share", "insomnia_night", "evening_greeting", "quiet_care"}
            or (busy_schedule and self._busy_voice_reason_eligible(reason))
        ):
            weight = 0.5 + (0.55 if action_profile["voicey"] else 0.0) + motive_bias["voice"] + affinity_bias["voice"]
            if action_profile["clingy"]:
                weight += 0.2
            if reason == "insomnia_night":
                weight += 0.28
            if busy_schedule:
                # A busy agenda makes a short voice note more convenient, but
                # the normal weighted choice still leaves text as the fallback.
                weight += 0.22
            weighted.append(("voice", weight))
        for ability in self._available_external_proactive_abilities(user):
            ability_name = str(ability.get("name") or "")
            if not ability_name:
                continue
            probability = max(0.0, min(1.0, _safe_float(ability.get("share_probability"), 0.0)))
            if probability <= 0:
                continue
            when_text = " ".join(str(ability.get(key) or "") for key in ("when", "use_for", "description"))
            weight = max(0.02, probability) * 0.9
            if reason in {"activity_share", "diary_share", "background_schedule", "state_share", "quiet_care"}:
                weight += probability * 0.35
            if motive and any(token and token in motive for token in re.split(r"[\s,，、/]+", when_text)[:16]):
                weight += probability * 0.25
            weighted.append((f"external:{ability_name}", weight))

        primary = self._weighted_choice(weighted)
        if primary != "message":
            combined = self._maybe_combine_actions(primary, reason, weather=weather, action_profile=action_profile, user=user)
            if combined:
                return self._fallback_action_for_unavailable(combined, user)
        return self._fallback_action_for_unavailable(primary, user)

    def _weighted_choice(self, items: list[tuple[str, float]]) -> str:
        filtered = [(name, max(0.0, float(weight))) for name, weight in items if name]
        if not filtered:
            return "message"
        total = sum(weight for _, weight in filtered)
        if total <= 0:
            return filtered[0][0]
        point = _engine_host.random.random() * total
        upto = 0.0
        for name, weight in filtered:
            upto += weight
            if point <= upto:
                return name
        return filtered[-1][0]

    def _maybe_combine_actions(
        self,
        primary: str,
        reason: str,
        *,
        weather: str = "",
        action_profile: dict[str, bool] | None = None,
        user: dict[str, Any] | None = None,
    ) -> str:
        profile = action_profile or self._persona_action_profile()
        candidates: list[tuple[str, float]] = []
        if primary == "photo_text" and self._voice_available(user) and reason in {"activity_share", "diary_share", "background_schedule"}:
            weight = 0.06
            if profile["clingy"] or profile["voicey"]:
                weight += 0.06
            if any(token in weather for token in ("晚霞", "雨", "晴", "阳光")):
                weight += 0.04
            candidates.append(("photo_text+voice", weight))
        if not candidates:
            return primary
        candidates.append((primary, 1.0))
        return self._weighted_choice(candidates)
