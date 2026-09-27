# -*- coding: utf-8 -*-
"""DailyStateWeatherAlertRefreshWeatherFetchMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（13 个方法 + 0 个模块级名字 + 0 个类级赋值 / 386 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import _now_ts, _openmeteo_weather_description, _qweather_alert_text, logger
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _safe_int
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import asyncio
from .daily_state_weather_shared import deepcopy
from .daily_state_weather_shared import math
from .daily_state_weather_shared import runtime_persona_setting
from .daily_state_weather_shared import urlencode



class DailyStateWeatherAlertRefreshWeatherFetchMixin:
    """DailyStateWeatherAlertRefreshWeatherFetchMixin（从 DailyStateWeatherMixin 拆出）。"""


    async def _maybe_refresh_weather_alerts(self, *, force: bool = False) -> dict[str, Any]:
        """Refresh QWeather alerts and enqueue owner-only, deduplicated notices."""

        if not bool(runtime_persona_setting(self, "enable_weather_context", True)) or not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {}
        lock = getattr(self, "_weather_alert_refresh_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._weather_alert_refresh_lock = lock
        async with lock:
            now = _now_ts()
            state = self.data.setdefault("weather_alert_awareness", {})
            if not isinstance(state, dict):
                state = {}
                self.data["weather_alert_awareness"] = state
            next_check = _safe_float(state.get("next_check_at"), 0)
            if not force and next_check > now:
                self._queue_weather_alert_pending_events(now=now)
                return deepcopy(self.data.get("weather_alerts", {}))
            interval_minutes = _safe_int(
                runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
                10,
                5,
                60,
            )
            state["next_check_at"] = now + interval_minutes * 60
            previous_cache = deepcopy(self.data.get("weather_alerts", {}))
            current_config_key = self._weather_alert_config_key()
            previous_config_key = _qweather_alert_text(
                previous_cache.get("config_key") if isinstance(previous_cache, dict) else "",
                96,
            )
            state_config_key = _qweather_alert_text(state.get("config_key"), 96)
            has_previous_alerts = bool(
                isinstance(previous_cache, dict)
                and isinstance(previous_cache.get("alerts"), list)
                and previous_cache.get("alerts")
            )
            config_changed = bool(
                (has_previous_alerts and previous_config_key != current_config_key)
                or (state_config_key and state_config_key != current_config_key)
            )
            if config_changed:
                # A location/host change invalidates both the baseline and
                # undelivered events from the previous place.
                state["initialized"] = False
                state["baseline_ids"] = []
                state["pending_events"] = []
            state["config_key"] = current_config_key
            result = await self._ensure_weather_alert_context(force=True)
            state["last_check_at"] = now
            if result.get("refreshed"):
                if config_changed:
                    state["initialized"] = False
                    state["pending_events"] = []
                initialized = bool(state.get("initialized"))
                if not initialized:
                    state["initialized"] = True
                    state["baseline_ids"] = [
                        self._weather_alert_identity(item)
                        for item in result.get("alerts", [])
                        if self._weather_alert_identity(item)
                    ][:64]
                else:
                    events = self._weather_alert_event_candidates(
                        previous_cache,
                        result,
                        now=now,
                        initialized=True,
                    )
                    self._weather_alert_append_pending_events(events)
                state["last_success_ts"] = _safe_float(result.get("last_success_ts"), now)
                state["last_error"] = ""
            else:
                state["last_error"] = _single_line(result.get("error"), 100)
                # A failed request retries sooner than a normal refresh but is
                # still bounded to avoid a tight loop during an outage.
                state["next_check_at"] = now + max(5 * 60, min(interval_minutes * 60, 30 * 60))
            offered = self._queue_weather_alert_pending_events(now=now)
            state["last_offered_count"] = offered
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                saver(
                    sections={
                        "weather_alert_awareness",
                        "users",
                        "proactive_candidate_pool",
                    }
                )
            return deepcopy(result)

    @classmethod
    def _weather_alerts_summary_text(
        cls,
        alerts: Any,
        *,
        min_severity: Any = "blue",
        max_items: int = 4,
    ) -> str:
        """Render a short, factual summary for a caller's prompt/context."""

        visible = cls._filter_weather_alerts(alerts, min_severity)
        lines: list[str] = []
        for alert in visible[: max(1, int(max_items))]:
            if not isinstance(alert, dict):
                continue
            level = _qweather_alert_text(alert.get("color") or alert.get("color_code") or alert.get("severity"), 24)
            event = _qweather_alert_text(alert.get("event") or "天气", 48)
            headline = _qweather_alert_text(alert.get("headline") or alert.get("description"), 160)
            if headline:
                lines.append(f"{level + ' ' if level else ''}{event}：{headline}")
        return "；".join(lines)

    def _qweather_weather_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_weather_token(self) -> str:
        raw = (
            getattr(self, "weather_token", "")
            or getattr(self, "qweather_token", "")
            or getattr(self, "weather_alert_token", "")
            or getattr(self, "weather_alert_jwt", "")
            or getattr(self, "weather_alert_api_key", "")
        )
        token = str(raw or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return token

    def _qweather_weather_headers(self) -> dict[str, str]:
        token = self._qweather_weather_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {"X-QW-Api-Key": token, "Accept": "application/json"}
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _qweather_weather_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude) for the QWeather coordinate query."""

        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        lat = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    def _build_qweather_weather_url(self, resolved: Any = None) -> str:
        host = self._qweather_weather_api_host()
        snapshot = resolved if isinstance(resolved, dict) else self._qweather_location_snapshot()
        location_id = _single_line(snapshot.get("location_id"), 40)
        location = self._qweather_weather_location(snapshot)
        if not host or (not location_id and location is None):
            return ""
        if location_id:
            location_text = location_id
        else:
            latitude, longitude = location
            # QWeather expects longitude first for coordinate locations.
            location_text = ",".join(
                (
                    self._qweather_alert_coordinate_text(longitude),
                    self._qweather_alert_coordinate_text(latitude),
                )
            )
        return host + "/v7/weather/now?" + urlencode(
            {"location": location_text, "lang": "zh", "unit": "m"}
        )

    @staticmethod
    def _parse_qweather_weather_payload(payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {"prompt": "", "source": ""}
        current = payload.get("now")
        if not isinstance(current, dict):
            return {"prompt": "", "source": ""}
        description = _single_line(current.get("text"), 80)
        if not description:
            return {"prompt": "", "source": ""}
        try:
            temperature = float(current.get("temp"))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if not math.isfinite(temperature):
            return {"prompt": "", "source": ""}
        details: list[str] = []
        optional_fields = (
            ("feelsLike", "体感", "°C"),
            ("windDir", "", ""),
            ("windScale", "风力", "级"),
            ("humidity", "湿度", "%"),
        )
        for key, label, suffix in optional_fields:
            value = _single_line(current.get(key), 24)
            if not value:
                continue
            if key in {"feelsLike", "humidity"}:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(numeric):
                    continue
                value = f"{numeric:g}"
            if key == "windDir":
                details.append(value)
            else:
                details.append(f"{label} {value}{suffix}")
        detail_text = "，" + "，".join(details) if details else ""
        return {
            "prompt": f"当前天气 {description}，约 {temperature:g}°C{detail_text}。",
            "source": "qweather",
        }

    async def _fetch_qweather_weather(self) -> dict[str, str]:
        host = self._qweather_weather_api_host()
        token = self._qweather_weather_token()
        resolved = await self._resolve_qweather_location()
        url = self._build_qweather_weather_url(resolved)
        if not host or not token or not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_weather_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("QWeather 实时天气请求失败: %s", response.status)
                        return {"prompt": "", "source": ""}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("QWeather 实时天气请求超时")
            return {"prompt": "", "source": ""}
        except Exception as exc:
            logger.debug("QWeather 实时天气获取失败: %s", _single_line(exc, 160))
            return {"prompt": "", "source": ""}
        parsed = self._parse_qweather_weather_payload(payload)
        if parsed.get("prompt"):
            label = _single_line(resolved.get("label") if isinstance(resolved, dict) else "", 120)
            if label:
                parsed["location_label"] = label
        return parsed

    async def _fetch_openmeteo_weather(self) -> dict[str, str]:
        try:
            lat = float(runtime_persona_setting(self, "weather_lat", 0))
            lon = float(runtime_persona_setting(self, "weather_lon", 0))
        except (TypeError, ValueError):
            return {"prompt": "", "source": ""}
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or lat == 0 and lon == 0
        ):
            return {"prompt": "", "source": ""}
        params = urlencode(
            {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,weather_code",
            }
        )
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"Open-Meteo 天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"Open-Meteo 天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict):
                return {"prompt": "", "source": ""}
            current = weather_data.get("current")
            if not isinstance(current, dict):
                return {"prompt": "", "source": ""}
            temperature = float(current["temperature_2m"])
            weather_code = int(current["weather_code"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            description = _openmeteo_weather_description(weather_code)
            return {
                "prompt": f"当前天气 {description},约 {temperature:g}°C。",
                "source": "openmeteo",
            }
        except (AttributeError, KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_amap_weather(self) -> dict[str, str]:
        key = str(getattr(self, "weather_amap_api_key", "") or "").strip()
        city = str(runtime_persona_setting(self, "weather_amap_city", "") or "").strip()
        if not key or not city:
            return {"prompt": "", "source": ""}
        url = "https://restapi.amap.com/v3/weather/weatherInfo?" + urlencode(
            {"key": key, "city": city, "extensions": "base", "output": "JSON"}
        )
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"高德天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"高德天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            if not isinstance(weather_data, dict) or str(weather_data.get("status")) != "1":
                return {"prompt": "", "source": ""}
            lives = weather_data.get("lives")
            live = lives[0] if isinstance(lives, list) and lives else None
            if not isinstance(live, dict) or not str(live.get("weather") or "").strip():
                return {"prompt": "", "source": ""}
            temperature = float(live["temperature"])
            if not math.isfinite(temperature):
                return {"prompt": "", "source": ""}
            return {
                "prompt": f"当前天气 {live['weather']}，约 {temperature:g}°C。",
                "source": "amap",
            }
        except (KeyError, TypeError, ValueError):
            return {"prompt": "", "source": ""}

    async def _fetch_own_weather_prompt(self) -> dict[str, str]:
        weather_source = str(runtime_persona_setting(self, "weather_source", "qweather") or "qweather").strip().lower()
        if weather_source == "qweather":
            return await self._fetch_qweather_weather()
        if weather_source == "amap":
            return await self._fetch_amap_weather()
        if weather_source == "openmeteo":
            return await self._fetch_openmeteo_weather()
        if not self.weather_api_key:
            return {"prompt": "", "source": ""}
        url = self._build_weather_url()
        if not url:
            return {"prompt": "", "source": ""}
        try:
            import aiohttp

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.debug(f"天气请求失败: {response.status}")
                        return {"prompt": "", "source": ""}
                    weather_data = await response.json()
        except Exception as e:
            logger.debug(f"私有天气获取失败: {e}")
            return {"prompt": "", "source": ""}
        try:
            weather_desc = weather_data.get("weather", [{}])[0].get("description", "")
            temp = weather_data.get("main", {}).get("temp", 0)
            if weather_desc:
                return {
                    "prompt": f"当前天气 {weather_desc},约 {temp}°C。",
                    "source": "private_companion",
                }
        except Exception:
            pass
        return {"prompt": "", "source": ""}

    def _build_weather_url(self) -> str:
        key = self.weather_api_key
        city = runtime_persona_setting(self, "weather_city", "")
        lat = runtime_persona_setting(self, "weather_lat", 0)
        lon = runtime_persona_setting(self, "weather_lon", 0)
        params = {
            "appid": key,
            "units": "metric",
            "lang": "zh_cn",
        }
        if city:
            params["q"] = city
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        if -90 <= lat <= 90 and -180 <= lon <= 180 and lat != 0 and lon != 0:
            params["lat"] = lat
            params["lon"] = lon
            return f"https://api.openweathermap.org/data/2.5/weather?{urlencode(params)}"
        return ""
