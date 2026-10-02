# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import time

import pytest

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.daily_state_tick import DailyStateTickMixin


class CircuitHarness(DailyStateMixin, DailyStateTickMixin):
    def __init__(self) -> None:
        self.data = {"users": {"user-a": {"enabled": True, "next_proactive_at": 10}}, "daily_state": {}}
        self._data_lock = asyncio.Lock()
        self.saved_sections: list[set[str]] = []
        self.rendered = False

    def _user_enabled_for_proactive(self, _user_id, _user):
        return True

    def _get_user(self, user_id):
        return self.data["users"].setdefault(user_id, {})

    def _save_data_sync(self, *, sections):
        self.saved_sections.append(set(sections))

    def _is_troubleshooting_proactive_plan(self, _user):
        return False

    def _due_internal_llm_timer_id(self, _user, *, now):
        self.rendered = True
        return ""


def test_two_recent_rejections_open_a_two_hour_platform_circuit():
    host = CircuitHarness()
    assert host._record_proactive_platform_send_circuit("retcode=1200 EventChecker sendMsg", now=1000) == 0
    until = host._record_proactive_platform_send_circuit("retcode=1200 EventChecker sendMsg", now=1030)
    assert until == 1030 + 2 * 3600
    assert host._proactive_platform_send_circuit_remaining(now=1000) == until - 1000
    assert host._record_proactive_platform_send_circuit("retcode=1404", now=1100) == 0


@pytest.mark.asyncio
async def test_open_platform_circuit_skips_user_generation_and_defers_task():
    host = CircuitHarness()
    user = host.data["users"]["user-a"]
    host.data["daily_state"]["proactive_platform_send_circuit"] = {
        "kind": "onebot_event_checker_rejection",
        "open_until": time.time() + 10000,
    }

    await host._tick_user("user-a", user)

    assert not host.rendered
    assert user["next_proactive_at"] > 0
    assert user["planned_proactive_window_start_at"] == user["next_proactive_at"]
    assert host.saved_sections == [{"users"}]
