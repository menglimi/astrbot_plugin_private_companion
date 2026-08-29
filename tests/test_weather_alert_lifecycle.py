# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from astrbot_plugin_private_companion.daily_state import DailyStateMixin


class _AlertLifecycleHarness(DailyStateMixin):
    enable_weather_context = True
    enable_weather_alerts = True
    weather_alert_api_host = "https://alerts.example.test"
    weather_alert_token = "test-token"
    weather_alert_refresh_minutes = 10
    weather_alert_min_severity = "blue"
    weather_lat = 39.9
    weather_lon = 116.4
    environment_perception_timezone = "Asia/Shanghai"

    def __init__(self) -> None:
        self.data = {
            "users": {
                "owner": {
                    "umo": "platform:FriendMessage:owner",
                    "relationship_role": "owner",
                    "enabled": True,
                },
                "friend": {
                    "umo": "platform:FriendMessage:friend",
                    "relationship_role": "friend",
                    "enabled": True,
                },
            },
            "weather_alerts": {},
            "weather_alert_awareness": {},
        }
        self.responses: list[dict] = []
        self.offered: list[tuple[str, dict]] = []
        self.generation_disabled = False
        self.reject_as_terminal = False

    async def _fetch_qweather_alerts(self) -> dict:
        payload = self.responses.pop(0)
        parsed = self._parse_qweather_alert_payload(payload)
        parsed["source"] = "qweather"
        return parsed

    def _save_data_sync(self, **_kwargs) -> None:
        return None

    def _private_user_role(self, user: dict, _user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")

    def _user_enabled_for_proactive(self, _user_id: str, user: dict) -> bool:
        return user.get("enabled") is not False

    def _proactive_generation_disabled(self) -> bool:
        return self.generation_disabled

    def _offer_proactive_candidate(self, user_id: str, _user: dict, candidate: dict) -> bool:
        self.offered.append((user_id, candidate))
        if self.reject_as_terminal:
            candidate["lifecycle_status"] = "skipped"
            candidate["lifecycle_note"] = "来源事件生成时区已变化"
            return False
        return True


def _payload(
    *,
    alert_id: str = "alert-1",
    headline: str = "雷电橙色预警",
    color: str = "orange",
    message_type: object = "Alert",
    supersedes: list[str] | None = None,
) -> dict:
    alert = {
        "id": alert_id,
        "senderName": "地方气象台",
        "eventType": {"name": "雷电", "code": "1103"},
        "color": {"code": color},
        "messageType": message_type,
        "issuedTime": "2026-07-26T08:00+08:00",
        "headline": headline,
        "description": "请注意防范",
        "instruction": "减少户外活动",
    }
    if supersedes is not None:
        alert["messageType"] = {"code": message_type, "supersedes": supersedes}
    return {
        "code": "200",
        "alerts": [alert],
    }


class WeatherAlertLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_fetch_is_baseline_then_update_and_resolution_are_offered_once(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.responses = [
            _payload(),
            _payload(headline="雷电橙色预警更新"),
            {"code": "200", "alerts": []},
            {"code": "200", "alerts": []},
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual([], harness.offered)

        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual(1, len(harness.offered))
        self.assertEqual("owner", harness.offered[-1][0])
        self.assertEqual("updated", harness.offered[-1][1]["context"]["kind"])

        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual(2, len(harness.offered))
        self.assertEqual("resolved", harness.offered[-1][1]["context"]["kind"])

        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual(2, len(harness.offered))

    async def test_cancellation_is_an_owner_only_event(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.responses = [
            _payload(color="red", headline="暴雨红色预警"),
            _payload(
                color="red",
                headline="暴雨红色预警解除",
                message_type={"code": "Cancel"},
            ),
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)

        self.assertEqual(1, len(harness.offered))
        user_id, candidate = harness.offered[0]
        self.assertEqual("owner", user_id)
        self.assertEqual("cancelled", candidate["context"]["kind"])

    async def test_terminal_resolution_does_not_repeat_after_cancellation(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.responses = [
            _payload(alert_id="old-alert", color="red", headline="暴雨红色预警"),
            _payload(
                alert_id="cancel-alert",
                color="",
                headline="暴雨红色预警解除",
                message_type="cancel",
                supersedes=["old-alert"],
            ),
            {"code": "200", "alerts": []},
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)

        self.assertEqual(1, len(harness.offered))
        self.assertEqual("cancelled", harness.offered[0][1]["context"]["kind"])

    async def test_superseding_update_is_one_event_not_update_plus_resolution(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.responses = [
            _payload(alert_id="old-alert", headline="雷电橙色预警"),
            _payload(
                alert_id="new-alert",
                headline="雷电橙色预警更新",
                message_type="update",
                supersedes=["old-alert"],
            ),
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)

        self.assertEqual(1, len(harness.offered))
        self.assertEqual("updated", harness.offered[0][1]["context"]["kind"])
        self.assertEqual("new-alert", harness.offered[0][1]["context"]["alert"]["id"])

    async def test_superseding_cancellation_keeps_previous_level_for_filtering(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.weather_alert_min_severity = "orange"
        harness.responses = [
            _payload(alert_id="old-alert", color="red", headline="暴雨红色预警"),
            _payload(
                alert_id="cancel-alert",
                color="",
                headline="暴雨红色预警解除",
                message_type="cancel",
                supersedes=["old-alert"],
            ),
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)

        self.assertEqual(1, len(harness.offered))
        self.assertEqual("cancelled", harness.offered[0][1]["context"]["kind"])

    async def test_minimum_level_and_zero_daily_gate_do_not_leak_to_friend(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.weather_alert_min_severity = "orange"
        harness.generation_disabled = True
        harness.responses = [
            _payload(color="yellow", headline="大风黄色预警"),
            _payload(color="orange", headline="大风橙色预警"),
            _payload(color="orange", headline="大风橙色预警"),
        ]

        await harness._maybe_refresh_weather_alerts(force=True)
        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual([], harness.offered)
        pending = harness.data["weather_alert_awareness"]["pending_events"]
        self.assertEqual(1, len(pending))

        harness.generation_disabled = False
        await harness._maybe_refresh_weather_alerts(force=True)
        self.assertEqual(1, len(harness.offered))
        self.assertEqual("owner", harness.offered[0][0])
        self.assertEqual("orange", harness.offered[0][1]["context"]["alert"]["color_code"])

    async def test_yesterdays_pending_alert_is_discarded_before_delivery(self) -> None:
        harness = _AlertLifecycleHarness()
        now = datetime.now(timezone.utc).timestamp()
        harness.data["weather_alert_awareness"]["pending_events"] = [{
            "event_key": "new:old-alert:fingerprint",
            "kind": "new",
            "captured_at": now - timedelta(days=1).total_seconds(),
            "alert": {"id": "old-alert", "color_code": "orange"},
        }]

        offered = harness._queue_weather_alert_pending_events(now=now)

        self.assertEqual(0, offered)
        self.assertEqual([], harness.offered)
        self.assertEqual([], harness.data["weather_alert_awareness"]["pending_events"])

    async def test_terminally_skipped_alert_is_consumed_and_not_recreated(self) -> None:
        harness = _AlertLifecycleHarness()
        harness.reject_as_terminal = True
        now = datetime.now(timezone.utc).timestamp()
        alert = {"id": "terminal-alert", "color_code": "orange"}
        event = {
            "event_key": "resolved:terminal-alert:fingerprint",
            "kind": "resolved",
            "captured_at": now,
            "alert": alert,
        }
        harness.data["weather_alert_awareness"]["pending_events"] = [event]

        offered = harness._queue_weather_alert_pending_events(now=now)

        self.assertEqual(offered, 0)
        self.assertEqual(harness.data["weather_alert_awareness"]["pending_events"], [])
        self.assertEqual(harness.offered[0][1]["window_timezone"], "Asia/Shanghai")
        terminal_identity = harness._weather_alert_terminal_identity(alert)
        self.assertIn(
            terminal_identity,
            harness.data["weather_alert_awareness"]["terminal_event_identities"],
        )
        harness._weather_alert_append_pending_events([event])
        self.assertEqual(harness.data["weather_alert_awareness"]["pending_events"], [])


if __name__ == "__main__":
    unittest.main()
