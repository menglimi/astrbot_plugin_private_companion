# -*- coding: utf-8 -*-
"""DailyStateWeatherAlertEventQueueMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 440 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import _now_ts, _qweather_alert_rank, _qweather_alert_text
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import datetime
from .daily_state_weather_shared import deepcopy
from .daily_state_weather_shared import hashlib
from .daily_state_weather_shared import math
from .daily_state_weather_shared import random
from .daily_state_weather_shared import runtime_persona_setting
from .daily_state_weather_shared import timezone



class DailyStateWeatherAlertEventQueueMixin:
    """DailyStateWeatherAlertEventQueueMixin（从 DailyStateWeatherMixin 拆出）。"""


    def _weather_alert_time_ts(self, value: Any) -> float:
        """Convert a provider time to the plugin's local epoch when possible."""

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) and parsed > 0 else 0.0
            except (TypeError, ValueError):
                return 0.0
        text = _qweather_alert_text(value, 96)
        if not text:
            return 0.0
        try:
            parsed = float(text)
            if math.isfinite(parsed) and parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
        normalized = text.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            current = datetime.fromisoformat(normalized)
        except (TypeError, ValueError):
            return 0.0
        if current.tzinfo is None:
            now_getter = getattr(self, "_environment_now", None)
            try:
                zone = now_getter().tzinfo if callable(now_getter) else None
            except Exception:
                zone = None
            current = current.replace(tzinfo=zone or timezone.utc)
        try:
            return float(current.timestamp())
        except (TypeError, ValueError, OSError):
            return 0.0

    def _weather_alert_is_expired(self, alert: Any, *, now: float | None = None) -> bool:
        if not isinstance(alert, dict):
            return True
        expire_ts = self._weather_alert_time_ts(alert.get("expire_time"))
        return expire_ts > 0 and expire_ts <= (_safe_float(now, _now_ts()))

    @staticmethod
    def _weather_alert_is_cancelled(alert: Any) -> bool:
        if not isinstance(alert, dict):
            return False
        if bool(alert.get("is_cancelled")):
            return True
        text = " ".join(
            _single_line(alert.get(key), 60).lower()
            for key in ("message_type", "status", "headline", "event")
        )
        return any(token in text for token in ("cancel", "撤销", "解除", "取消"))

    def _active_weather_alerts(
        self,
        alerts: Any,
        *,
        now: float | None = None,
        include_cancelled: bool = False,
    ) -> list[dict[str, Any]]:
        current = _safe_float(now, _now_ts())
        result: list[dict[str, Any]] = []
        for alert in self._dedupe_weather_alerts(alerts):
            if not isinstance(alert, dict):
                continue
            if not include_cancelled and self._weather_alert_is_cancelled(alert):
                continue
            if not include_cancelled and self._weather_alert_is_expired(alert, now=current):
                continue
            result.append(alert)
        return result

    def _weather_alert_owner_users(self) -> list[tuple[str, dict[str, Any]]]:
        users = self.data.get("users") if isinstance(getattr(self, "data", None), dict) else {}
        if not isinstance(users, dict):
            return []
        targets: list[tuple[str, dict[str, Any]]] = []
        for raw_user_id, user in users.items():
            user_id = str(raw_user_id or "").strip()
            if not user_id or not isinstance(user, dict) or not user.get("umo"):
                continue
            role_getter = getattr(self, "_private_user_role", None)
            try:
                role = role_getter(user, user_id) if callable(role_getter) else str(user.get("relationship_role") or "")
            except TypeError:
                role = role_getter(user) if callable(role_getter) else str(user.get("relationship_role") or "")
            except Exception:
                role = str(user.get("relationship_role") or "")
            if str(role or "").strip().lower() != "owner":
                continue
            enabled_getter = getattr(self, "_user_enabled_for_proactive", None)
            if callable(enabled_getter):
                try:
                    if not enabled_getter(user_id, user):
                        continue
                except Exception:
                    continue
            targets.append((user_id, user))
        return targets

    @staticmethod
    def _weather_alert_event_key(kind: Any, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        identity = _single_line(alert.get("id") or alert.get("fingerprint"), 180)
        fingerprint = _single_line(alert.get("fingerprint"), 80)
        return ":".join(part for part in (_single_line(kind, 20), identity, fingerprint) if part)

    def _weather_alert_context_for_event(
        self,
        alert: dict[str, Any],
        *,
        kind: str,
        now: float,
    ) -> dict[str, Any]:
        level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
        event = _qweather_alert_text(alert.get("event") or "天气", 48)
        title = _qweather_alert_text(alert.get("headline") or alert.get("description"), 220)
        instruction = _qweather_alert_text(alert.get("instruction"), 500)
        if kind in {"cancelled", "resolved"}:
            status = "已解除"
        elif kind == "expired" or self._weather_alert_is_expired(alert, now=now):
            status = "已过期或解除"
        elif kind == "updated":
            status = "刚更新"
        else:
            status = "刚发布"
        return {
            "kind": _single_line(kind, 20),
            "status": status,
            "alert": deepcopy(alert),
            "id": _single_line(alert.get("id") or alert.get("fingerprint"), 180),
            "level": level,
            "event": event,
            "title": title,
            "instruction": instruction,
            "captured_at": now,
        }

    def _weather_alert_event_candidates(
        self,
        previous_cache: Any,
        current_cache: dict[str, Any],
        *,
        now: float,
        initialized: bool,
    ) -> list[dict[str, Any]]:
        """Turn a cache transition into bounded, deduplicated pending events."""

        if not initialized or not isinstance(current_cache, dict):
            return []
        old_items = self._dedupe_weather_alerts(
            previous_cache.get("alerts", []) if isinstance(previous_cache, dict) else []
        )
        current_items = self._dedupe_weather_alerts(current_cache.get("alerts", []))
        old_by_id = {self._weather_alert_identity(item): item for item in old_items if self._weather_alert_identity(item)}
        current_by_id = {self._weather_alert_identity(item): item for item in current_items if self._weather_alert_identity(item)}
        new_ids = set(current_cache.get("new_alert_ids") or [])
        updated_ids = set(current_cache.get("updated_alert_ids") or [])
        resolved_ids = set(current_cache.get("resolved_alert_ids") or [])
        events: list[dict[str, Any]] = []
        threshold = runtime_persona_setting(self, "weather_alert_min_severity", "blue")

        # QWeather represents an updated warning as a new object whose
        # ``messageType.supersedes`` points at the previous warning ID.  Keep
        # that transition as one update event instead of emitting a new
        # warning followed by a misleading "old warning resolved" notice.
        superseded_by_current: dict[str, dict[str, Any]] = {}
        for current_item in current_items:
            supersedes = current_item.get("supersedes")
            if not isinstance(supersedes, list):
                continue
            for superseded_id in supersedes:
                identity = str(superseded_id or "").strip()
                if identity and identity in old_by_id:
                    superseded_by_current[identity] = current_item
        handled_current_ids: set[str] = set()

        def is_update(item: dict[str, Any]) -> bool:
            message_type = _qweather_alert_text(item.get("message_type"), 64).lower()
            if any(token in message_type for token in ("update", "amend", "extend", "replace", "续发", "变更")):
                return True
            return bool(item.get("supersedes"))

        def add(
            kind: str,
            item: dict[str, Any],
            *,
            policy_item: dict[str, Any] | None = None,
        ) -> None:
            if not isinstance(item, dict):
                return
            # Cancellation is useful even though it is not an active warning;
            # all other events must pass the configured minimum color/severity.
            if policy_item is None and self._weather_alert_is_cancelled(item):
                supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
                policy_item = next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                )
            if not self._filter_weather_alerts([policy_item or item], threshold):
                return
            key = self._weather_alert_event_key(kind, item)
            if not key or any(existing.get("event_key") == key for existing in events):
                return
            context = self._weather_alert_context_for_event(item, kind=kind, now=now)
            context["event_key"] = key
            events.append(context)

        for identity in sorted(new_ids):
            item = current_by_id.get(str(identity))
            if item:
                kind = "cancelled" if self._weather_alert_is_cancelled(item) else ("updated" if is_update(item) else "new")
                add(kind, item)
                handled_current_ids.add(str(identity))
        for identity in sorted(updated_ids):
            item = current_by_id.get(str(identity))
            if item:
                add("cancelled" if self._weather_alert_is_cancelled(item) else "updated", item)
                handled_current_ids.add(str(identity))
        for identity in sorted(resolved_ids):
            item = old_by_id.get(str(identity))
            if not item:
                continue
            if str(identity) in superseded_by_current:
                # The replacement event above carries the current facts and
                # is the only user-facing transition needed.
                continue
            # A provider can remove an item a few seconds before its explicit
            # expiry. Use the old object as the factual basis either way.
            add("expired" if self._weather_alert_is_expired(item, now=now) else "resolved", item)
        # A replacement may explicitly reference an older warning ID even if
        # the provider still returns both objects for one response.
        for item in current_items:
            current_identity = self._weather_alert_identity(item)
            if current_identity in handled_current_ids:
                continue
            supersedes = item.get("supersedes") if isinstance(item.get("supersedes"), list) else []
            if not supersedes:
                continue
            if self._weather_alert_is_cancelled(item):
                add("cancelled", item, policy_item=next(
                    (old_by_id.get(str(value)) for value in supersedes if str(value) in old_by_id),
                    None,
                ))
            elif any(str(value) in old_by_id for value in supersedes):
                add("updated", item)
        rank_getter = lambda value: _qweather_alert_rank(value.get("color_code") or value.get("severity"))
        events.sort(key=lambda value: rank_getter(value.get("alert", {})), reverse=True)
        return events[:12]

    def _weather_alert_event_captured_at(self, event: dict[str, Any]) -> float:
        """Return the best available observation time for a pending alert."""
        if not isinstance(event, dict):
            return 0.0
        captured_at = _safe_float(event.get("captured_at"), 0)
        if captured_at > 0:
            return captured_at
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        return self._weather_alert_time_ts(
            alert.get("issued_time") or alert.get("effective_time") or alert.get("onset_time")
        )

    def _weather_alert_append_pending_events(self, events: list[dict[str, Any]]) -> None:
        state = self.data.setdefault("weather_alert_awareness", {})
        if not isinstance(state, dict):
            state = {}
            self.data["weather_alert_awareness"] = state
        pending = state.get("pending_events")
        if not isinstance(pending, list):
            pending = []
            state["pending_events"] = pending
        terminal_history = state.get("terminal_event_identities")
        if not isinstance(terminal_history, dict):
            terminal_history = {}
            state["terminal_event_identities"] = terminal_history
        terminal_cutoff = _now_ts() - 7 * 24 * 3600
        for identity, captured_at in list(terminal_history.items()):
            if _safe_float(captured_at, 0) < terminal_cutoff:
                terminal_history.pop(identity, None)
        known = {
            _single_line(item.get("event_key"), 260)
            for item in pending
            if isinstance(item, dict) and _single_line(item.get("event_key"), 260)
        }
        for event in events:
            if not isinstance(event, dict):
                continue
            key = _single_line(event.get("event_key"), 260)
            kind = _single_line(event.get("kind"), 20)
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity and terminal_identity in terminal_history:
                continue
            if key and key not in known:
                pending.append(deepcopy(event))
                known.add(key)
        # A provider can emit both a resolved and an expired representation for
        # the same warning in one refresh. Keep only the first terminal event.
        terminal_seen: set[str] = set()
        compact_pending: list[dict[str, Any]] = []
        for item in pending:
            if not isinstance(item, dict):
                continue
            kind = _single_line(item.get("kind"), 20)
            alert = item.get("alert") if isinstance(item.get("alert"), dict) else {}
            terminal_identity = self._weather_alert_terminal_identity(alert) if kind in {
                "cancelled", "resolved", "expired"
            } else ""
            if terminal_identity:
                if terminal_identity in terminal_seen:
                    continue
                terminal_seen.add(terminal_identity)
            compact_pending.append(item)
        pending[:] = compact_pending
        # Weather changes are time-sensitive. An outage or a disabled daily
        # quota must not turn yesterday's alert into today's proactive message.
        cutoff = _now_ts() - 6 * 3600
        pending[:] = [
            item
            for item in pending
            if isinstance(item, dict)
            and (
                self._weather_alert_event_captured_at(item) <= 0
                or self._weather_alert_event_captured_at(item) >= cutoff
            )
        ]
        pending.sort(
            key=lambda item: _qweather_alert_rank(
                (
                    (item.get("alert") or {}).get("color_code")
                    or (item.get("alert") or {}).get("color")
                    or (item.get("alert") or {}).get("severity")
                )
                if isinstance(item.get("alert"), dict)
                else ""
            ),
            reverse=True,
        )
        del pending[20:]

    def _weather_alert_candidate_delay(self, event: dict[str, Any], *, now: float) -> tuple[float, float]:
        alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
        rank = _qweather_alert_rank(alert.get("color_code") or alert.get("color") or alert.get("severity"))
        if event.get("kind") in {"cancelled", "resolved", "expired"}:
            return now + random.uniform(1.0, 4.0) * 60.0, 90 * 60.0
        if rank >= 3:
            return now + random.uniform(20.0, 90.0), 30 * 60.0
        if rank >= 2:
            return now + random.uniform(1.0, 5.0) * 60.0, 60 * 60.0
        return now + random.uniform(5.0, 18.0) * 60.0, 3 * 3600.0

    def _queue_weather_alert_pending_events(self, *, now: float) -> int:
        offer = getattr(self, "_offer_proactive_candidate", None)
        if not callable(offer):
            return 0
        if callable(getattr(self, "_proactive_generation_disabled", None)):
            try:
                if self._proactive_generation_disabled():
                    return 0
            except Exception:
                pass
        state = self.data.get("weather_alert_awareness")
        if not isinstance(state, dict):
            return 0
        pending = state.get("pending_events")
        if not isinstance(pending, list) or not pending:
            return 0
        owners = self._weather_alert_owner_users()
        if not owners:
            return 0
        offered = 0
        remaining: list[dict[str, Any]] = []
        owner_ids = {user_id for user_id, _ in owners}
        for event in pending:
            if not isinstance(event, dict):
                continue
            alert = event.get("alert") if isinstance(event.get("alert"), dict) else {}
            captured_at = self._weather_alert_event_captured_at(event)
            if captured_at > 0 and now - captured_at > 6 * 3600:
                continue
            if alert and self._weather_alert_is_expired(alert, now=now) and event.get("kind") not in {"cancelled", "resolved", "expired"}:
                continue
            delivered = event.get("delivered_user_ids")
            if not isinstance(delivered, list):
                delivered = []
                event["delivered_user_ids"] = delivered
            for user_id, user in owners:
                if user_id in delivered:
                    continue
                scheduled, lifetime = self._weather_alert_candidate_delay(event, now=now)
                level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
                topic = _qweather_alert_text(
                    f"{level}{event.get('event') or '天气'}{event.get('status') or '有变化'}",
                    90,
                )
                alert_event_key = _single_line(event.get("event_key"), 260)
                candidate = {
                    "source": "weather_alert",
                    "reason": "weather_alert",
                    "action": "message",
                    "window_timezone": self._weather_window_timezone(),
                    "scheduled_ts": scheduled,
                    "window_start_at": scheduled,
                    "preferred_ts": scheduled,
                    "best_until_at": scheduled + min(lifetime, 60 * 60),
                    "expire_at": scheduled + lifetime,
                    "topic": topic,
                    "motive": "刚收到一条与当前位置有关的官方气象预警，想把最重要的一点及时告诉主要用户",
                    "score": max(72, min(100, 70 + _qweather_alert_rank(alert.get("color_code") or alert.get("severity")) * 10)),
                    "origin_event_id": (
                        "weather:" + hashlib.sha1(alert_event_key.encode("utf-8", errors="ignore")).hexdigest()[:24]
                        if alert_event_key
                        else ""
                    ),
                    "context_key": "planned_weather_alert_context",
                    "context": deepcopy(event),
                }
                if offer(user_id, user, candidate):
                    delivered.append(user_id)
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
                    offered += 1
                elif candidate.get("lifecycle_status") in {"skipped", "expired"}:
                    # Consume terminal candidates. Otherwise an old-timezone
                    # terminal alert would be rebuilt on every refresh.
                    delivered.append(user_id)
                    lifecycle_note = _single_line(candidate.get("lifecycle_note"), 180)
                    if lifecycle_note:
                        skip_reasons = event.setdefault("terminal_skip_reasons", {})
                        if isinstance(skip_reasons, dict):
                            skip_reasons[user_id] = lifecycle_note
                    if event.get("kind") in {"cancelled", "resolved", "expired"}:
                        terminal_history = state.setdefault("terminal_event_identities", {})
                        if isinstance(terminal_history, dict):
                            terminal_identity = self._weather_alert_terminal_identity(alert)
                            if terminal_identity:
                                terminal_history[terminal_identity] = now
            if owner_ids and owner_ids.issubset(set(delivered)):
                continue
            remaining.append(event)
        state["pending_events"] = remaining
        return offered
