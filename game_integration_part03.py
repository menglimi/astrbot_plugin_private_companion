# -*- coding: utf-8 -*-
"""GameIntegrationPart03Mixin。

由 tools/split_mixin_domain.py 从 game_integration.py 机械抽取（1 个方法 + 0 个模块级名字 + 0 个类级赋值 / 192 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 GameIntegrationMixin）。
"""
from __future__ import annotations

from .game_integration_shared import GAME_STATE_VERSION, logger
from copy import deepcopy
from typing import Any



class GameIntegrationPart03Mixin:
    """GameIntegrationPart03Mixin（从 GameIntegrationMixin 拆出）。"""


    async def _record_external_game_event_scoped(
        self,
        event: dict[str, Any],
        descriptor: dict[str, str],
        now: float,
        event_id: str,
    ) -> dict[str, Any]:
        async with self._game_event_lock(descriptor["scope_key"], event["user_id"]):
            async with self._game_data_guard():
                user = self._get_user(event["user_id"])
                scopes = self._game_scope_store(user)
                previous = self._game_state_from_store(user, descriptor)
                processed = self._game_processed_ids(previous)
                if event_id in processed:
                    return self._game_public_result(previous, duplicate=True)
                user_snapshot = {
                    key: self._game_json_safe(user.get(key))
                    for key in (
                        "user_id",
                        "nickname",
                        "display_name",
                        "style",
                        "relationship_role",
                        "relationship_mode",
                        "current_interaction",
                        "umo",
                    )
                }
                if self._game_event_is_stale(previous, event, now):
                    self._game_add_processed_id(previous, event_id, now)
                    previous.update(descriptor)
                    previous["version"] = GAME_STATE_VERSION
                    scopes[descriptor["scope_key"]] = previous
                    self._game_prune_scope_store(
                        scopes,
                        keep_key=descriptor["scope_key"],
                        now=now,
                    )
                    user["game_afterglow"] = deepcopy(previous)
                    persisted = True
                    try:
                        self._save_data_sync(sections={"users"})
                    except Exception as exc:
                        persisted = False
                        logger.warning("游戏旧事件回执保存失败: %s", self._game_clean_text(exc, 120))
                    return self._game_public_result(previous, stale=True, persisted=persisted)

            streak_result, streak_count = self._game_afterglow_streak(previous, event, now=now)
            assessment = await self._assess_external_game_afterglow(
                event,
                previous,
                streak_count=streak_count,
                user_snapshot=user_snapshot,
            )

            async with self._game_data_guard():
                user = self._get_user(event["user_id"])
                scopes = self._game_scope_store(user)
                current = self._game_state_from_store(user, descriptor)
                processed = self._game_processed_ids(current)
                if event_id in processed:
                    return self._game_public_result(current, duplicate=True)
                if self._game_event_is_stale(current, event, now):
                    self._game_add_processed_id(current, event_id, now)
                    current.update(descriptor)
                    current["version"] = GAME_STATE_VERSION
                    scopes[descriptor["scope_key"]] = current
                    self._game_prune_scope_store(
                        scopes,
                        keep_key=descriptor["scope_key"],
                        now=now,
                    )
                    user["game_afterglow"] = deepcopy(current)
                    persisted = True
                    try:
                        self._save_data_sync(sections={"users"})
                    except Exception:
                        persisted = False
                    return self._game_public_result(current, stale=True, persisted=persisted)

                competition_cap = self._game_bounded_int(assessment.get("competition_cap"), 30, 0, 100)
                companionship_cap = self._game_bounded_int(assessment.get("companionship_cap"), 50, 0, 100)
                previous_expiry = self._game_finite_float(current.get("expires_at"), 0.0)
                afterglow_was_active = previous_expiry > now
                base_competition = self._game_bounded_int(current.get("competition_charge"), 0, -100, 100) if afterglow_was_active else 0
                base_companionship = self._game_bounded_int(current.get("companionship_warmth"), 0, 0, 100) if afterglow_was_active else 0
                competition = max(-competition_cap, min(competition_cap, base_competition + self._game_bounded_int(assessment.get("competition_delta"), 0, -40, 40)))
                companionship = max(0, min(companionship_cap, base_companionship + self._game_bounded_int(assessment.get("companionship_delta"), 0, 0, 40)))
                duration_seconds = self._game_bounded_int(assessment.get("duration_minutes"), 0, 0, 10080) * 60
                effect = self._game_clean_text(assessment.get("rematch_effect"), 20).lower()
                expires_at = max(previous_expiry if afterglow_was_active else now, now + duration_seconds)
                if event["event_type"] == "rematch_requested":
                    if effect == "clear":
                        competition = 0
                        companionship = 0
                        expires_at = now
                        streak_result, streak_count = "", 0
                    elif effect == "shorten":
                        target_expiry = now + duration_seconds
                        expires_at = min(previous_expiry, target_expiry) if afterglow_was_active else target_expiry
                    elif effect == "extend":
                        expires_at = max(previous_expiry if afterglow_was_active else now, now + duration_seconds)
                    elif effect == "keep" and afterglow_was_active:
                        expires_at = previous_expiry
                    elif not afterglow_was_active:
                        expires_at = now + duration_seconds

                stats = self._game_json_safe(current.get("stats")) if isinstance(current.get("stats"), dict) else {}
                if not isinstance(stats, dict):
                    stats = {}
                if event["event_type"] == "round_finished":
                    stats["rounds"] = self._game_bounded_int(stats.get("rounds"), 0, 0) + 1
                    result_key = {"bot_win": "bot_wins", "bot_loss": "bot_losses", "draw": "draws", "completed": "completed"}.get(event["bot_result"], "completed")
                    stats[result_key] = self._game_bounded_int(stats.get(result_key), 0, 0) + 1
                game_label = (
                    self._game_prompt_text(event.get("game_label"), 40, event["game"])
                    if event.get("game_label_supplied")
                    else self._game_prompt_text(current.get("game_label"), 40, event["game"])
                )
                preserve_rematch_streak = (
                    event["event_type"] == "rematch_requested"
                    and effect != "clear"
                    and afterglow_was_active
                    and expires_at > now
                )
                updated: dict[str, Any] = {
                    "version": GAME_STATE_VERSION,
                    **descriptor,
                    "game_label": game_label,
                    "competition_charge": competition,
                    "companionship_warmth": companionship,
                    "competition_cap": competition_cap,
                    "companionship_cap": companionship_cap,
                    "tone": self._game_prompt_text(assessment.get("tone"), 160),
                    "reflection": self._game_prompt_text(assessment.get("reflection"), 240),
                    "invite_interest": self._game_bounded_int(assessment.get("invite_interest"), 0, 0, 100),
                    "streak_result": (
                        streak_result
                        if event["event_type"] == "round_finished"
                        else self._game_clean_text(current.get("streak_result"), 24)
                        if preserve_rematch_streak
                        else ""
                    ),
                    "streak_count": (
                        streak_count
                        if event["event_type"] == "round_finished"
                        else self._game_bounded_int(current.get("streak_count"), 0, 0, 999)
                        if preserve_rematch_streak
                        else 0
                    ),
                    "last_result": self._game_clean_text(event.get("bot_result"), 24),
                    "last_event_type": self._game_clean_text(event.get("event_type"), 40),
                    "last_event_at": self._game_finite_float(event.get("occurred_at"), now),
                    "last_match_id": self._game_clean_text(event.get("match_id"), 160),
                    "last_round_number": self._game_bounded_int(event.get("round_number"), 0, 0, 100000),
                    "updated_at": now,
                    "expires_at": self._game_finite_float(expires_at, now),
                    "stats": stats,
                    "last_event": {
                        key: self._game_json_safe(game_label if key == "game_label" else event.get(key))
                        for key in ("event_type", "game", "game_label", "bot_result", "room_id", "match_id", "round_number", "request_text")
                    },
                }
                self._game_add_processed_id(updated, event_id, now)
                # Moving an updated scope to the end gives equal timestamps a
                # stable write-order tie-breaker without changing stored data.
                scopes.pop(descriptor["scope_key"], None)
                scopes[descriptor["scope_key"]] = updated
                self._game_prune_scope_store(
                    scopes,
                    keep_key=descriptor["scope_key"],
                    now=now,
                )
                user["game_afterglow"] = deepcopy(updated)
                persisted = True
                try:
                    self._save_data_sync(sections={"users"})
                except Exception as exc:
                    persisted = False
                    logger.warning("游戏余韵保存失败: %s", self._game_clean_text(exc, 120))

            logger.info(
                "游戏余韵已结算: user=%s scope=%s game=%s result=%s streak=%s competition=%s companionship=%s",
                event["user_id"],
                descriptor["scope_key"],
                event["game"],
                event["bot_result"],
                updated["streak_count"],
                competition,
                companionship,
            )
            return self._game_public_result(updated, persisted=persisted)
