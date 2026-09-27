# -*- coding: utf-8 -*-
"""GameIntegrationPart01Mixin。

由 tools/split_mixin_domain.py 从 game_integration.py 机械抽取（18 个方法 + 0 个模块级名字 + 0 个类级赋值 / 449 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GameIntegrationMixin）。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from .game_integration_shared import (
    GAME_EVENT_TYPES,
    GAME_PROCESSED_EVENT_LIMIT,
    GAME_RESULTS,
    GAME_SCOPE_KEY_VERSION,
    GAME_SCOPE_RETENTION_SECONDS,
    GAME_SCOPE_STORE_LIMIT,
    GAME_STATE_VERSION,
)
from typing import Any



class GameIntegrationPart01Mixin:
    """GameIntegrationPart01Mixin（从 GameIntegrationMixin 拆出）。"""


    @staticmethod
    def _game_clean_text(value: Any, limit: int = 160) -> str:
        text = str(value or "").replace("\x00", " ")
        text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[: max(0, int(limit or 0))]

    @classmethod
    def _game_clean_persona_id(cls, value: Any) -> str:
        return unicodedata.normalize("NFC", cls._game_clean_text(value, 96))

    @classmethod
    def _game_json_safe(cls, value: Any, *, depth: int = 0) -> Any:
        """Keep external/plugin data small, JSON-safe and non-executable."""
        if value is None or isinstance(value, (bool, int, str)):
            if isinstance(value, str):
                return cls._game_clean_text(value, 160)
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if depth >= 2:
            return cls._game_clean_text(value, 80)
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, raw_value in list(value.items())[:24]:
                key = cls._game_clean_text(raw_key, 48)
                if not key:
                    continue
                result[key] = cls._game_json_safe(raw_value, depth=depth + 1)
            return result
        if isinstance(value, (list, tuple, set)):
            return [cls._game_json_safe(item, depth=depth + 1) for item in list(value)[:24]]
        return cls._game_clean_text(value, 80)

    @classmethod
    def _game_json_object(cls, raw: Any) -> dict[str, Any]:
        """Parse plain, fenced or embedded JSON without trusting its contents."""
        if isinstance(raw, dict):
            value = raw
        else:
            text = str(raw or "").strip()
            if not text:
                return {}
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
            decoder = json.JSONDecoder(parse_constant=lambda _name: None)
            value = None
            try:
                value = json.loads(text, parse_constant=lambda _name: None)
            except (TypeError, ValueError, json.JSONDecodeError):
                for index, char in enumerate(text):
                    if char != "{":
                        continue
                    try:
                        candidate, _ = decoder.raw_decode(text[index:])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(candidate, dict):
                        value = candidate
                        break
        return cls._game_json_safe(value) if isinstance(value, dict) else {}

    @classmethod
    def _game_derived_event_id(cls, event: dict[str, Any]) -> str:
        identity = {
            key: event.get(key)
            for key in (
                "event_type",
                "persona_id",
                "user_id",
                "game",
                "match_id",
                "bot_result",
                "room_id",
                "session_id",
                "scope",
                "round_number",
                "request_text",
                "score",
            )
        }
        if event.get("occurred_at_supplied"):
            identity["occurred_at"] = cls._game_finite_float(
                event.get("occurred_at"),
                0.0,
            )
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return "game:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:48]

    @classmethod
    def _normalize_external_game_event(cls, payload: Any) -> dict[str, Any]:
        source = payload if isinstance(payload, dict) else {}
        event_type = cls._game_clean_text(source.get("event_type"), 40).lower()
        user_id = cls._game_clean_text(source.get("user_id"), 80)
        game = cls._game_clean_text(source.get("game"), 40).lower() or "unknown"
        result = cls._game_clean_text(source.get("bot_result"), 24).lower()
        scope = cls._game_clean_text(source.get("scope"), 20).lower()
        scope = {"dm": "private", "direct": "private", "群": "group", "群聊": "group"}.get(scope, scope)
        session_id = cls._game_clean_text(source.get("session_id"), 200)
        room_id = cls._game_clean_text(source.get("room_id"), 100)
        if not scope:
            scope = "group" if room_id or ":GroupMessage:" in session_id else "private"
        if event_type not in GAME_EVENT_TYPES or not user_id:
            return {}
        if scope and scope not in {"private", "group"}:
            return {}
        if event_type == "round_finished" and result not in GAME_RESULTS:
            return {}
        if event_type == "rematch_requested" and result not in GAME_RESULTS:
            result = "completed"
        occurred_raw = source.get("occurred_at")
        occurred_supplied = occurred_raw not in (None, "")
        occurred_at = cls._game_finite_float(occurred_raw, 0.0)
        game_label_supplied = bool(cls._game_clean_text(source.get("game_label"), 40))
        supplied_event_id = cls._game_clean_text(source.get("event_id"), 160)
        normalized = {
            "event_type": event_type,
            "event_id": supplied_event_id,
            "event_id_supplied": bool(supplied_event_id),
            "persona_id": cls._game_clean_persona_id(source.get("persona_id")),
            "user_id": user_id,
            "user_name": cls._game_clean_text(source.get("user_name"), 80),
            "game": game,
            "game_label": cls._game_prompt_text(source.get("game_label"), 40, game or "游戏"),
            "game_label_supplied": game_label_supplied,
            "bot_result": result,
            "request_text": cls._game_prompt_text(source.get("request_text"), 240),
            "recent_context": cls._game_prompt_text(source.get("recent_context"), 900),
            "room_id": room_id,
            "session_id": session_id,
            "scope": scope,
            "difficulty": cls._game_clean_text(source.get("difficulty"), 24),
            "match_id": cls._game_clean_text(source.get("match_id"), 160),
            "round_number": cls._game_bounded_int(source.get("round_number"), 0, 0, 100000),
            "score": cls._game_json_safe(source.get("score")) if isinstance(source.get("score"), dict) else {},
            "occurred_at": occurred_at,
            "occurred_at_supplied": occurred_supplied,
            "source_plugin": cls._game_clean_text(source.get("source_plugin"), 100) or "external",
        }
        if not normalized["event_id"]:
            normalized["event_id"] = cls._game_derived_event_id(normalized)
        return normalized

    def _game_current_persona_id(self, event: dict[str, Any] | None = None) -> str:
        source = event or {}
        event_persona = self._game_clean_persona_id(source.get("persona_id"))
        multi_persona = bool(getattr(self, "enable_multi_persona_mode", False))
        configured_getter = getattr(self, "_configured_multi_persona_ids", None)
        configured_order: list[str] = []
        configured: set[str] = set()
        configured_known = multi_persona and callable(configured_getter)
        if configured_known:
            try:
                configured_order = [
                    self._game_clean_persona_id(item)
                    for item in configured_getter()
                    if self._game_clean_persona_id(item)
                ]
                configured = set(configured_order)
            except Exception:
                configured_known = False
                configured_order = []
                configured = set()
        if multi_persona and event_persona:
            if not configured_known or event_persona in configured:
                return event_persona
            return ""
        for getter_name in ("_active_persona_scope", "_effective_plugin_persona_id"):
            getter = getattr(self, getter_name, None)
            if callable(getter):
                try:
                    value = self._game_clean_persona_id(getter())
                except Exception:
                    value = ""
                if value:
                    if not multi_persona or not configured_known or value in configured:
                        return value
        primary_getter = getattr(self, "_primary_persona_id", None)
        try:
            primary = self._game_clean_persona_id(
                primary_getter()
                if callable(primary_getter)
                else getattr(self, "plugin_specific_persona_id", "")
            )
        except Exception:
            primary = ""
        if primary and (not multi_persona or not configured_known or primary in configured):
            return primary
        if multi_persona and configured_order:
            return configured_order[0]
        return "default"

    async def _game_run_in_persona(
        self,
        persona_id: str,
        callback: Any,
        *args: Any,
    ) -> dict[str, Any]:
        token = None
        activator = getattr(self, "_activate_persona_id", None)
        deactivator = getattr(self, "_deactivate_persona_for_event", None)
        if bool(getattr(self, "enable_multi_persona_mode", False)) and callable(activator):
            token = activator(persona_id)
        try:
            return await callback(*args)
        finally:
            if token is not None and callable(deactivator):
                deactivator(token)

    @classmethod
    def _game_conversation_id(cls, event: dict[str, Any]) -> str:
        session = cls._game_clean_text(event.get("session_id"), 200)
        room_id = cls._game_clean_text(event.get("room_id"), 100)
        scope = cls._game_clean_text(event.get("scope"), 20).lower()
        group_match = re.search(r":GroupMessage:([^:]+)$", session)
        friend_match = re.search(r":FriendMessage:([^:]+)$", session)
        if scope == "group":
            if room_id:
                return "group:" + room_id
            if group_match:
                return "group:" + group_match.group(1)
            return session or "group:unknown"
        if friend_match:
            return "private:" + friend_match.group(1)
        if session:
            return session
        return "private:" + cls._game_clean_text(event.get("user_id"), 80)

    @classmethod
    def _game_scope_descriptor(cls, event: dict[str, Any], persona_id: str) -> dict[str, str]:
        session_id = cls._game_clean_text(event.get("session_id"), 200)
        scope = cls._game_clean_text(event.get("scope"), 20).lower()
        if not scope:
            scope = "group" if event.get("room_id") or ":GroupMessage:" in session_id else "private"
        conversation_id = cls._game_conversation_id({**event, "scope": scope})
        descriptor = {
            "persona_id": cls._game_clean_persona_id(persona_id) or "default",
            "scope": scope,
            "conversation_id": cls._game_clean_text(conversation_id, 220),
            "game": cls._game_clean_text(event.get("game"), 40).lower() or "unknown",
            "legacy_default": "1" if cls._game_is_default_scope(event) else "0",
        }
        identity = json.dumps(
            {
                key: descriptor[key]
                for key in ("persona_id", "scope", "conversation_id", "game")
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        descriptor["scope_key"] = GAME_SCOPE_KEY_VERSION + ":" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]
        return descriptor

    @staticmethod
    def _game_is_default_scope(event: dict[str, Any]) -> bool:
        return not event.get("session_id") and not event.get("room_id") and str(event.get("scope") or "") != "group"

    @staticmethod
    def _game_scope_store(user: dict[str, Any]) -> dict[str, Any]:
        raw = user.get("game_afterglow_scopes") if isinstance(user, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
            if isinstance(user, dict):
                user["game_afterglow_scopes"] = raw
        return raw

    @classmethod
    def _game_normalize_stored_state(cls, value: Any) -> dict[str, Any]:
        """Normalize internal state without applying external JSON item limits."""
        if not isinstance(value, dict):
            return {}

        state: dict[str, Any] = {}
        text_limits = {
            "persona_id": 96,
            "scope": 20,
            "conversation_id": 220,
            "scope_key": 80,
            "game": 40,
            "game_label": 40,
            "tone": 160,
            "reflection": 240,
            "streak_result": 24,
            "last_result": 24,
            "last_event_type": 40,
            "last_match_id": 160,
            "legacy_default": 1,
        }
        for key, limit in text_limits.items():
            if key not in value:
                continue
            if key == "persona_id":
                state[key] = cls._game_clean_persona_id(value.get(key))
            elif key in {"tone", "reflection"}:
                state[key] = cls._game_prompt_text(value.get(key), limit)
            else:
                state[key] = cls._game_clean_text(value.get(key), limit)

        integer_fields = {
            "version": (1, GAME_STATE_VERSION),
            "competition_charge": (-100, 100),
            "companionship_warmth": (0, 100),
            "competition_cap": (0, 100),
            "companionship_cap": (0, 100),
            "invite_interest": (0, 100),
            "streak_count": (0, 999),
            "last_round_number": (0, 100000),
        }
        for key, (minimum, maximum) in integer_fields.items():
            if key in value:
                state[key] = cls._game_bounded_int(value.get(key), 0, minimum, maximum)

        for key in ("last_event_at", "updated_at", "expires_at"):
            if key in value:
                state[key] = cls._game_finite_float(value.get(key), 0.0)

        raw_stats = value.get("stats")
        if isinstance(raw_stats, dict):
            stats: dict[str, int] = {}
            for raw_key, raw_count in list(raw_stats.items())[:24]:
                key = cls._game_clean_text(raw_key, 48)
                if key:
                    stats[key] = cls._game_bounded_int(raw_count, 0, 0, 10**9)
            state["stats"] = stats

        raw_last_event = value.get("last_event")
        if isinstance(raw_last_event, dict):
            last_event: dict[str, Any] = {}
            for key, limit in {
                "event_type": 40,
                "game": 40,
                "game_label": 40,
                "bot_result": 24,
                "room_id": 100,
                "match_id": 160,
                "request_text": 240,
            }.items():
                if key in raw_last_event:
                    last_event[key] = cls._game_clean_text(raw_last_event.get(key), limit)
            if "round_number" in raw_last_event:
                last_event["round_number"] = cls._game_bounded_int(
                    raw_last_event.get("round_number"), 0, 0, 100000
                )
            state["last_event"] = last_event

        processed = cls._game_processed_ids(value)
        if processed:
            state["processed_event_ids"] = processed
            state["recent_event_ids"] = list(processed)[-128:]
        elif isinstance(value.get("recent_event_ids"), list):
            recent = [
                cls._game_clean_text(item, 180)
                for item in value["recent_event_ids"][-128:]
            ]
            state["recent_event_ids"] = [item for item in recent if item]
        return state

    @classmethod
    def _game_state_from_store(cls, user: dict[str, Any], descriptor: dict[str, str]) -> dict[str, Any]:
        scopes = cls._game_scope_store(user)
        value = scopes.get(descriptor.get("scope_key", ""))
        if isinstance(value, dict):
            return cls._game_normalize_stored_state(value)
        if descriptor.get("legacy_default") == "1":
            legacy = user.get("game_afterglow") if isinstance(user, dict) else {}
            if isinstance(legacy, dict):
                normalized = cls._game_normalize_stored_state(legacy)
                legacy_scope_key = cls._game_clean_text(normalized.get("scope_key"), 80)
                if legacy_scope_key:
                    return normalized if legacy_scope_key == descriptor.get("scope_key") else {}
                if scopes:
                    return {}
                for key in ("persona_id", "scope", "conversation_id", "game"):
                    actual = cls._game_clean_text(normalized.get(key), 220)
                    expected = cls._game_clean_text(descriptor.get(key), 220)
                    if actual and expected and actual != expected:
                        return {}
                return normalized
        return {}

    @classmethod
    def _game_processed_ids(cls, state: dict[str, Any]) -> dict[str, float]:
        raw = state.get("processed_event_ids") if isinstance(state, dict) else {}
        result: dict[str, float] = {}
        if isinstance(raw, dict):
            for key, value in list(raw.items())[-GAME_PROCESSED_EVENT_LIMIT:]:
                clean = cls._game_clean_text(key, 180)
                if clean:
                    result[clean] = cls._game_finite_float(value, 0.0)
        elif isinstance(raw, list):
            for key in raw[-GAME_PROCESSED_EVENT_LIMIT:]:
                clean = cls._game_clean_text(key, 180)
                if clean:
                    result[clean] = 0.0
        recent = state.get("recent_event_ids") if isinstance(state, dict) else []
        if isinstance(recent, list):
            for key in recent[-128:]:
                clean = cls._game_clean_text(key, 180)
                if clean:
                    result.setdefault(clean, 0.0)
        return dict(list(result.items())[-GAME_PROCESSED_EVENT_LIMIT:])

    @classmethod
    def _game_add_processed_id(cls, state: dict[str, Any], event_id: str, now: float) -> None:
        processed = cls._game_processed_ids(state)
        processed[cls._game_clean_text(event_id, 180)] = cls._game_finite_float(now, 0.0)
        state["processed_event_ids"] = dict(list(processed.items())[-GAME_PROCESSED_EVENT_LIMIT:])
        state["recent_event_ids"] = list(state["processed_event_ids"].keys())[-128:]

    @classmethod
    def _game_prune_scope_store(
        cls,
        scopes: dict[str, Any],
        *,
        keep_key: str,
        now: float,
    ) -> None:
        current = cls._game_finite_float(now, time.time())
        candidates: list[tuple[float, str]] = []
        for raw_key, raw_state in list(scopes.items()):
            key = cls._game_clean_text(raw_key, 80)
            if not key or not isinstance(raw_state, dict):
                if raw_key != keep_key:
                    scopes.pop(raw_key, None)
                continue
            state = cls._game_normalize_stored_state(raw_state)
            touched_at = max(
                cls._game_finite_float(state.get("updated_at"), 0.0),
                cls._game_finite_float(state.get("last_event_at"), 0.0),
            )
            expires_at = cls._game_finite_float(state.get("expires_at"), 0.0)
            if (
                raw_key != keep_key
                and touched_at > 0
                and expires_at <= current
                and current - touched_at > GAME_SCOPE_RETENTION_SECONDS
            ):
                scopes.pop(raw_key, None)
                continue
            if raw_key != keep_key:
                candidates.append((touched_at, raw_key))
        overflow = len(scopes) - GAME_SCOPE_STORE_LIMIT
        if overflow > 0:
            for _touched_at, key in sorted(candidates)[:overflow]:
                scopes.pop(key, None)

    @classmethod
    def _game_state_matches_event(cls, state: dict[str, Any], event: dict[str, Any], descriptor: dict[str, str]) -> bool:
        if not isinstance(state, dict):
            return False
        for key in ("persona_id", "scope", "conversation_id", "game"):
            if key == "persona_id":
                expected = cls._game_clean_persona_id(descriptor.get(key))
                actual = cls._game_clean_persona_id(state.get(key))
            else:
                expected = cls._game_clean_text(descriptor.get(key), 220)
                actual = cls._game_clean_text(state.get(key), 220)
            if actual and expected and actual != expected:
                return False
        return True
