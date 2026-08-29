# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.helpers import _normalize_timezone_name
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _TimezoneStateHarness(ProactiveMixin):
    environment_perception_timezone = "Asia/Tokyo"

    def __init__(self) -> None:
        self.data = {
            "proactive_runtime": {"window_timezone": "Asia/Shanghai"},
            "proactive_candidate_pool": [
                {"id": "ordinary", "source": "story", "status": "accepted"},
                {"id": "timer", "source": "timer", "status": "accepted"},
            ],
            "users": {
                "ordinary": {
                    "next_proactive_at": 123,
                    "planned_proactive_source": "story",
                    "planned_candidate_id": "ordinary",
                    "planned_weather_alert_context": {"id": "weather"},
                    "proactive_impulses": [
                        {"id": "impulse", "source": "story", "state": "queued"}
                    ],
                },
                "timer": {
                    "next_proactive_at": 456,
                    "planned_proactive_source": "timer",
                    "planned_candidate_id": "timer",
                    "proactive_impulses": [
                        {"id": "timer-impulse", "source": "timer", "state": "queued"}
                    ],
                },
            },
            "weather_alert_awareness": {
                "pending_events": [{"event_key": "old"}],
                "terminal_event_identities": {"terminal": 99},
            },
            "environment_change_awareness": {"last_change": {"kind": "rain"}},
            "daily_weather": {"prompt": "old"},
            "weather_alerts": {"alerts": [{"id": "old"}]},
        }

    @staticmethod
    def _normalize_legacy_proactive_text(value, *, limit: int = 80) -> str:
        return str(value or "").strip().lower()[:limit]

    @staticmethod
    def _clear_planned_proactive_trigger(user: dict) -> None:
        user["planned_proactive_trigger_message_id"] = ""
        user["planned_proactive_trigger_umo"] = ""
        user["planned_proactive_trigger_ts"] = 0
        user["planned_proactive_trigger_inbound_count"] = -1


class TimezoneConfigTests(unittest.TestCase):
    def test_timezone_normalization_accepts_iana_and_rejects_invalid_names(self) -> None:
        self.assertEqual(_normalize_timezone_name("Asia/Tokyo"), "Asia/Tokyo")
        self.assertEqual(_normalize_timezone_name("invalid/timezone"), "Asia/Shanghai")
        self.assertEqual(_normalize_timezone_name(""), "Asia/Shanghai")

    def test_page_normalizes_main_and_deepseek_timezones(self) -> None:
        api = PrivateCompanionPageApi(SimpleNamespace())
        self.assertEqual(api._normalize_setting_value("environment_perception_timezone", "Asia/Tokyo"), "Asia/Tokyo")
        self.assertEqual(api._normalize_setting_value("environment_perception_timezone", "invalid/timezone"), "Asia/Shanghai")
        self.assertEqual(api._normalize_setting_value("deepseek_peak_timezone", "Europe/London"), "Europe/London")

    def test_applying_main_timezone_updates_runtime_date_boundary(self) -> None:
        plugin = SimpleNamespace(config={})
        api = PrivateCompanionPageApi(plugin)
        with patch(
            "astrbot_plugin_private_companion.runtime_config_dispatcher._set_today_key_timezone"
        ) as set_timezone:
            api._apply_config_value("environment_perception_timezone", "Asia/Tokyo")
        self.assertEqual(plugin.environment_perception_timezone, "Asia/Tokyo")
        set_timezone.assert_called_once_with("Asia/Tokyo")
        stored = plugin.config.get("environment_perception_timezone")
        if stored is None:
            stored = plugin.config.get("basic_config", {}).get("environment_perception_timezone")
        self.assertEqual(stored, "Asia/Tokyo")

    def test_timezone_change_invalidates_only_derived_wall_clock_state(self) -> None:
        harness = _TimezoneStateHarness()

        result = harness._invalidate_timezone_derived_state(
            "Asia/Shanghai",
            "Asia/Tokyo",
            schedule_save=False,
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["cleared_plans"], 1)
        self.assertEqual(result["blocked_candidates"], 1)
        self.assertEqual(
            harness.data["proactive_candidate_pool"][0]["lifecycle_status"],
            "skipped",
        )
        self.assertEqual(
            harness.data["proactive_candidate_pool"][1]["status"],
            "accepted",
        )
        self.assertEqual(
            harness.data["users"]["ordinary"]["proactive_impulses"][0]["state"],
            "blocked",
        )
        self.assertEqual(harness.data["users"]["ordinary"]["next_proactive_at"], 0)
        self.assertEqual(harness.data["users"]["timer"]["next_proactive_at"], 456)
        self.assertEqual(
            harness.data["weather_alert_awareness"]["terminal_event_identities"],
            {"terminal": 99},
        )
        self.assertEqual(harness.data["weather_alert_awareness"]["pending_events"], [])
        self.assertEqual(harness.data["daily_weather"], {})
        self.assertEqual(
            harness.data["proactive_runtime"]["window_timezone"],
            "Asia/Tokyo",
        )


if __name__ == "__main__":
    unittest.main()
