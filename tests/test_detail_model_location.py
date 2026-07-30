# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import datetime

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.helpers import _now_ts, _today_key


class _DetailLocationHarness(DailyStateMixin):
    def __init__(self) -> None:
        self.data = {
            "daily_state": {
                "date": _today_key(),
                "location": "工作场所",
                "location_source": "dialogue_override",
                "location_override_ts": _now_ts(),
            },
            "daily_plan": {
                "date": _today_key(),
                "items": [
                    {
                        "time": "20:50",
                        "end": "21:50",
                        "activity": "冲好牛奶，靠在宿舍床头等消息",
                    }
                ],
            },
            "detail_enhanced_segments": {
                f"{_today_key()}:0:20:50": {
                    "status": "done",
                    "location": "宿舍卧室",
                    "location_basis": ["coarse_plan", "state"],
                    "location_confidence": 0.93,
                }
            },
        }

    def _current_detail_segment_for_update(self):
        return {"key": f"{_today_key()}:0:20:50"}

    @staticmethod
    def _environment_now() -> datetime:
        return datetime.now()


class DetailModelLocationTests(unittest.TestCase):
    def test_current_location_prefers_detail_model_output(self) -> None:
        harness = _DetailLocationHarness()

        self.assertEqual("宿舍卧室", harness._current_location_state_text(harness.data["daily_state"]))

    def test_detail_model_location_updates_state_and_releases_stale_override(self) -> None:
        harness = _DetailLocationHarness()
        detail = {
            "location": "宿舍卧室",
            "location_basis": ["coarse_plan", "state"],
            "location_confidence": 0.93,
        }

        changed = harness._refresh_daily_state_location_from_plan(
            plan=harness.data["daily_plan"],
            detail=detail,
        )

        state = harness.data["daily_state"]
        self.assertTrue(changed)
        self.assertEqual("宿舍卧室", state["location"])
        self.assertEqual("detail_model", state["location_source"])
        self.assertEqual(0.93, state["location_confidence"])
        self.assertEqual(0.0, state["location_override_ts"])


if __name__ == "__main__":
    unittest.main()
