# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from astrbot_plugin_private_companion.scene_context import SceneContextMixin


class _SceneAlertHarness(SceneContextMixin):
    weather_alert_min_severity = "yellow"

    def __init__(self) -> None:
        self.data = {
            "daily_state": {"date": "2026-07-26", "energy": 70, "mood_bias": "平稳"},
            "daily_plan": {"date": "2026-07-26", "items": []},
            "daily_weather": {"prompt": "当前天气多云，约 27°C", "source": "openmeteo"},
            "weather_alerts": {
                "source": "qweather",
                "fetched_ts": 100,
                "alerts": [
                    {
                        "id": "warning-1",
                        "event": "雷电",
                        "color": "橙色",
                        "color_code": "orange",
                        "headline": "雷电橙色预警",
                        "instruction": "尽量避免户外活动",
                        "expire_time": "",
                    },
                    {
                        "id": "warning-2",
                        "event": "大风",
                        "color": "蓝色",
                        "color_code": "blue",
                        "headline": "大风蓝色预警",
                    },
                ],
            },
            "users": {"owner": {"nickname": "主人", "relationship_role": "owner"}},
        }

    @staticmethod
    def _scene_context_now() -> datetime:
        return datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def _weather_summary_text(weather: dict) -> str:
        return str(weather.get("prompt") or "")

    @staticmethod
    def _filter_weather_alerts(alerts, minimum):
        rank = {"blue": 0, "yellow": 1, "orange": 2, "red": 3}
        threshold = rank.get(str(minimum).lower(), 0)
        return [
            item
            for item in alerts
            if isinstance(item, dict)
            and rank.get(str(item.get("color_code") or "").lower(), 0) >= threshold
        ]

    @staticmethod
    def _qweather_alert_rank(value):
        return {"blue": 0, "蓝色": 0, "yellow": 1, "黄色": 1, "orange": 2, "橙色": 2, "red": 3, "红色": 3}.get(str(value).lower(), 0)

    @staticmethod
    def _get_current_plan_item(_plan):
        return {}

    @staticmethod
    def _private_user_role(user, _user_id=""):
        return str(user.get("relationship_role") or "friend")


class WeatherAlertSceneContextTests(unittest.TestCase):
    def test_snapshot_exposes_filtered_alerts_without_provider_credentials(self) -> None:
        harness = _SceneAlertHarness()
        snapshot = harness._build_companion_scene_snapshot(
            {"user_id": "owner", "nickname": "主人", "relationship_role": "owner"},
            now=harness._scene_context_now(),
        )

        self.assertEqual(1, snapshot["weather_alerts"]["count"])
        self.assertEqual("橙色", snapshot["weather_alerts"]["highest_level"])
        self.assertNotIn("token", str(snapshot))
        rendered = harness._format_companion_scene_snapshot(snapshot)
        self.assertIn("气象预警背景", rendered)
        self.assertIn("雷电橙色预警", rendered)
        self.assertNotIn("API Host", rendered)

    def test_secondary_user_does_not_receive_location_specific_alerts(self) -> None:
        harness = _SceneAlertHarness()
        snapshot = harness._build_companion_scene_snapshot(
            {"user_id": "friend", "nickname": "朋友", "relationship_role": "friend"},
            now=harness._scene_context_now(),
        )
        self.assertEqual(0, snapshot["weather_alerts"]["count"])
        self.assertNotIn("气象预警背景", harness._format_companion_scene_snapshot(snapshot))


if __name__ == "__main__":
    unittest.main()
