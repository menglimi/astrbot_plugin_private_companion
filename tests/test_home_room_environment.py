# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from astrbot_plugin_private_companion.home_room_environment import home_room_environment
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


def host(**changes):
    weather = {"prompt": "当前天气 小雨，约 18°C，体感 17°C", "source": "qweather", "location_label": "杭州", "fetched_ts": time.time() - 120, "config_key": "hangzhou", "token": "never-export-this"}
    values = dict(data={"daily_weather": weather}, _weather_window_timezone=lambda: "Asia/Shanghai", _weather_context_config_key=lambda: "hangzhou", _ensure_weather_context=AsyncMock(return_value=weather), enable_weather_context=True, weather_refresh_minutes=90)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_room_weather_uses_existing_service_and_only_exposes_display_fields():
    plugin = host()
    response = await PrivateCompanionPageApi(plugin).get_home_room_environment()
    data = response["data"]
    plugin._ensure_weather_context.assert_awaited_once_with()
    assert data["timezone"] == "Asia/Shanghai"
    assert data["weather"]["status"] == "live"
    assert data["weather"]["condition"] == "rain"
    assert data["weather"]["temperature_c"] == 18
    assert data["weather"]["location"] == "杭州"
    assert "never-export-this" not in json.dumps(data)
    assert "config_key" not in json.dumps(data)


@pytest.mark.asyncio
async def test_room_weather_failure_keeps_only_same_place_cache_and_marks_stale():
    plugin = host(_ensure_weather_context=AsyncMock(side_effect=RuntimeError("https://provider/?key=secret")))
    data = await home_room_environment(plugin)
    assert data["weather"]["status"] == "stale"
    assert data["weather"]["location"] == "杭州"
    assert "secret" not in json.dumps(data)
    plugin._weather_context_config_key = lambda: "beijing"
    data = await home_room_environment(plugin)
    assert data["weather"]["status"] == "unavailable"
    assert data["weather"]["location"] == ""
    assert data["weather"]["summary"] == ""


@pytest.mark.asyncio
async def test_disabled_weather_never_fetches_and_does_not_return_cached_weather():
    plugin = host(enable_weather_context=False)
    data = await home_room_environment(plugin)
    plugin._ensure_weather_context.assert_not_awaited()
    assert data["weather"]["status"] == "disabled"
    assert data["weather"]["condition"] == "unknown"
    assert data["weather"]["updated_at"] is None


@pytest.mark.asyncio
async def test_weather_timeout_cancels_provider_and_retains_explicit_old_observation():
    plugin = host()
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    plugin._ensure_weather_context = slow
    data = await home_room_environment(plugin, timeout=.01)
    assert cancelled.is_set()
    assert data["weather"]["status"] == "stale"


@pytest.mark.asyncio
async def test_settings_change_during_fetch_never_relabels_previous_location():
    plugin = host()

    async def changing():
        plugin._weather_context_config_key = lambda: "beijing"
        return plugin.data["daily_weather"]

    plugin._ensure_weather_context = changing
    data = await home_room_environment(plugin)
    assert data["weather"]["status"] == "unavailable"
    assert data["weather"]["location"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(("summary", "condition", "temperature"), [("Heavy snow, -4°C", "snow", -4), ("晴，气温 22℃", "clear", 22), ("阴天", "cloudy", None), ("雷阵雨，25摄氏度", "storm", 25), ("fog", "mist", None)])
async def test_provider_descriptions_keep_real_temperature_and_condition(summary, condition, temperature):
    plugin = host()
    plugin.data["daily_weather"]["prompt"] = summary
    data = await home_room_environment(plugin)
    assert data["weather"]["condition"] == condition
    assert data["weather"]["temperature_c"] == temperature


@pytest.mark.asyncio
async def test_missing_or_future_timestamp_and_invalid_timezone_are_safe():
    plugin = host(_weather_window_timezone=lambda: "global")
    plugin.data["daily_weather"]["fetched_ts"] = time.time() + 3600
    data = await home_room_environment(plugin)
    assert data["timezone"] == "Asia/Shanghai"
    assert data["weather"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_old_valid_cache_is_explicitly_stale():
    plugin = host()
    plugin.data["daily_weather"]["fetched_ts"] = time.time() - 7200
    data = await home_room_environment(plugin)
    assert data["weather"]["status"] == "stale"
