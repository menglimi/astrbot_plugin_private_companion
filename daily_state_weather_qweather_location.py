# -*- coding: utf-8 -*-
"""DailyStateWeatherQweatherLocationMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（20 个方法 + 0 个模块级名字 + 0 个类级赋值 / 415 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import _now_ts, _qweather_alert_rank, logger
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import asyncio
from .daily_state_weather_shared import deepcopy
from .daily_state_weather_shared import hashlib
from .daily_state_weather_shared import math
from .daily_state_weather_shared import re
from .daily_state_weather_shared import runtime_persona_setting
from .daily_state_weather_shared import unicodedata
from .daily_state_weather_shared import urlencode
from .daily_state_weather_shared import urlparse



class DailyStateWeatherQweatherLocationMixin:
    """DailyStateWeatherQweatherLocationMixin（从 DailyStateWeatherMixin 拆出）。"""


    @staticmethod
    def _qweather_alert_rank(value: Any) -> int:
        return _qweather_alert_rank(value)

    @staticmethod
    def _normalize_qweather_api_host(value: Any) -> str:
        """Normalize a QWeather API Host while refusing malformed URLs."""

        raw = str(value or "").strip()
        if not raw:
            return ""
        if "://" not in raw:
            raw = "https://" + raw
        try:
            parsed = urlparse(raw)
        except Exception:
            return ""
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return ""
        if parsed.scheme.lower() == "http":
            # QWeather credentials must not be sent to a remote plaintext
            # endpoint. Keep local development proxies usable without
            # weakening the default for arbitrary hosts.
            hostname = (parsed.hostname or "").strip("[]").lower()
            if hostname not in {"localhost", "127.0.0.1", "::1"}:
                return ""
        # Credentials in an API Host are never useful and could accidentally
        # be written to logs or persisted with the cache.
        if parsed.username or parsed.password:
            return ""
        path = (parsed.path or "").rstrip("/")
        for suffix in (
            "/geo/v2/city/lookup",
            "/geo/v2/city",
            "/geo/v2",
            "/weatheralert/v1/current",
            "/weatheralert/v1",
            "/v7/weather/now",
            "/v7/weather",
            "/v7/warning",
            "/v7",
        ):
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                break
        return f"{parsed.scheme.lower()}://{parsed.netloc}{path}".rstrip("/")

    def _qweather_alert_api_host(self) -> str:
        return self._normalize_qweather_api_host(
            getattr(self, "weather_api_host", "")
            or getattr(self, "qweather_api_host", "")
            or getattr(self, "weather_alert_api_host", "")
        )

    def _qweather_alert_token(self) -> str:
        # weather_alert_token is the documented name. Keep the aliases for
        # older local configs, but never persist or log the resulting
        # credential.
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

    def _qweather_alert_credential_kind(self, credential: Any = None) -> str:
        """Choose the QWeather auth header without exposing the credential.

        The current Weather Alert API accepts either a JWT or an API Key, but
        the two schemes must never be sent together.  Normal configuration
        uses the unambiguous JWT shape (three dot-separated segments) and
        treats other non-empty values as API Keys.
        """

        value = self._qweather_alert_token() if credential is None else str(credential or "").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value:
            return ""
        segments = value.split(".")
        if len(segments) == 3 and all(segment.strip() for segment in segments):
            return "jwt"
        return "api_key"

    @staticmethod
    def _qweather_alert_coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < minimum or number > maximum:
            return None
        return number

    def _qweather_configured_location(self) -> str:
        raw = _single_line(runtime_persona_setting(self, "weather_location", ""), 180)
        if not raw:
            return ""
        return unicodedata.normalize("NFKC", raw).strip().replace("，", ",")

    def _qweather_coordinates_from_text(self, value: Any) -> tuple[float, float] | None:
        """Parse QWeather's documented ``longitude,latitude`` format."""

        text = unicodedata.normalize("NFKC", str(value or "")).strip().replace("，", ",")
        pieces = [part.strip() for part in text.split(",")]
        if len(pieces) != 2:
            return None
        longitude = self._qweather_alert_coordinate(pieces[0], minimum=-180, maximum=180)
        latitude = self._qweather_alert_coordinate(pieces[1], minimum=-90, maximum=90)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    @staticmethod
    def _qweather_is_location_id(value: Any) -> bool:
        # Current QWeather LocationIDs are numeric (for example 101010100).
        # Keeping the range bounded avoids treating a short numeric city name
        # or an arbitrary long identifier as a provider ID.
        return bool(re.fullmatch(r"\d{7,18}", str(value or "").strip()))

    def _qweather_legacy_weather_location(self) -> tuple[float, float] | None:
        latitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lat", 0),
            minimum=-90,
            maximum=90,
        )
        longitude = self._qweather_alert_coordinate(
            runtime_persona_setting(self, "weather_lon", 0),
            minimum=-180,
            maximum=180,
        )
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return None
        return latitude, longitude

    def _qweather_location_identity(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                return "coordinates:" + ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
            return "configured:" + configured.casefold()
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return ""
        latitude, longitude = legacy
        return "legacy:" + ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )

    def _qweather_location_cache_key(self) -> str:
        identity = self._qweather_location_identity()
        host = self._qweather_alert_api_host()
        if not identity or not host:
            return ""
        raw = f"{host}|{identity}"
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def _qweather_cached_location(self, *, allow_stale: bool = True) -> dict[str, Any]:
        data = getattr(self, "data", None)
        cached = data.get("qweather_location") if isinstance(data, dict) else None
        if not isinstance(cached, dict) or cached.get("config_key") != self._qweather_location_cache_key():
            return {}
        location_id = _single_line(cached.get("location_id"), 40)
        latitude = self._qweather_alert_coordinate(cached.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(cached.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        fetched_ts = _safe_float(cached.get("fetched_ts"), 0)
        if not allow_stale and (fetched_ts <= 0 or _now_ts() - fetched_ts > 30 * 24 * 60 * 60):
            return {}
        return {
            "version": 1,
            "config_key": str(cached.get("config_key") or ""),
            "location_id": location_id,
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(cached.get("label"), 120),
            "fetched_ts": fetched_ts,
        }

    def _qweather_direct_location(self) -> dict[str, Any]:
        configured = self._qweather_configured_location()
        config_key = self._qweather_location_cache_key()
        if configured:
            coordinates = self._qweather_coordinates_from_text(configured)
            if coordinates is not None:
                latitude, longitude = coordinates
                label = ",".join(
                    (
                        self._qweather_alert_coordinate_text(longitude),
                        self._qweather_alert_coordinate_text(latitude),
                    )
                )
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": "",
                    "lat": latitude,
                    "lon": longitude,
                    "label": label,
                    "fetched_ts": _now_ts(),
                }
            if self._qweather_is_location_id(configured):
                return {
                    "version": 1,
                    "config_key": config_key,
                    "location_id": configured,
                    "lat": None,
                    "lon": None,
                    "label": configured,
                    "fetched_ts": 0,
                }
            return {}
        legacy = self._qweather_legacy_weather_location()
        if legacy is None:
            return {}
        latitude, longitude = legacy
        label = ",".join(
            (
                self._qweather_alert_coordinate_text(longitude),
                self._qweather_alert_coordinate_text(latitude),
            )
        )
        return {
            "version": 1,
            "config_key": config_key,
            "location_id": "",
            "lat": latitude,
            "lon": longitude,
            "label": label,
            "fetched_ts": _now_ts(),
        }

    def _qweather_location_snapshot(self) -> dict[str, Any]:
        cached = self._qweather_cached_location()
        if cached:
            return cached
        return self._qweather_direct_location()

    def _build_qweather_geo_lookup_url(self, query: Any = None) -> str:
        host = self._qweather_alert_api_host()
        configured = self._qweather_configured_location() if query is None else _single_line(query, 180)
        if not host or not configured:
            return ""
        params: dict[str, str | int] = {"location": configured, "number": 1, "lang": "zh"}
        if not self._qweather_is_location_id(configured) and self._qweather_coordinates_from_text(configured) is None:
            pieces = [part.strip() for part in configured.split(",")]
            if len(pieces) == 2 and all(pieces):
                params["location"] = pieces[0]
                params["adm"] = pieces[1]
        return host + "/geo/v2/city/lookup?" + urlencode(params)

    def _parse_qweather_location_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or str(payload.get("code") or "") != "200":
            return {}
        locations = payload.get("location")
        item = locations[0] if isinstance(locations, list) and locations else None
        if not isinstance(item, dict):
            return {}
        latitude = self._qweather_alert_coordinate(item.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(item.get("lon"), minimum=-180, maximum=180)
        if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            return {}
        labels: list[str] = []
        for key in ("name", "adm2", "adm1"):
            value = _single_line(item.get(key), 60)
            if value and value not in labels:
                labels.append(value)
        return {
            "location_id": _single_line(item.get("id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": "，".join(labels) or self._qweather_configured_location(),
        }

    async def _fetch_qweather_location_lookup(self, query: str) -> dict[str, Any]:
        url = self._build_qweather_geo_lookup_url(query)
        token = self._qweather_alert_token()
        if not url or not token:
            return {}
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        logger.debug("和风天气地点解析请求失败: %s", response.status)
                        return {}
                    try:
                        payload = await response.json()
                    except TypeError:
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气地点解析请求超时")
            return {}
        except Exception as exc:
            logger.debug("和风天气地点解析失败: %s", _single_line(exc, 160))
            return {}
        return self._parse_qweather_location_payload(payload)

    async def _store_qweather_location(
        self,
        resolved: dict[str, Any],
        *,
        expected_config_key: str = "",
    ) -> dict[str, Any]:
        current_config_key = self._qweather_location_cache_key()
        config_key = str(expected_config_key or current_config_key)
        latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
        longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
        if (
            not config_key
            or (expected_config_key and current_config_key != expected_config_key)
            or latitude is None
            or longitude is None
            or (latitude == 0 and longitude == 0)
        ):
            return {}
        record = {
            "version": 1,
            "config_key": config_key,
            "location_id": _single_line(resolved.get("location_id"), 40),
            "lat": latitude,
            "lon": longitude,
            "label": _single_line(resolved.get("label"), 120),
            "fetched_ts": _now_ts(),
        }

        stored = False

        def store() -> None:
            nonlocal stored
            data = getattr(self, "data", None)
            # Config can change while waiting for the shared data lock. Keep
            # the record tied to the request that produced it, never to a new
            # location selected while the request was in flight.
            if not isinstance(data, dict) or self._qweather_location_cache_key() != config_key:
                return
            data["qweather_location"] = deepcopy(record)
            stored = True
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"qweather_location"})
                except Exception as exc:
                    logger.debug("保存和风天气地点缓存失败: %s", _single_line(exc, 160))

        data_lock = getattr(self, "_data_lock", None)
        if isinstance(data_lock, asyncio.Lock):
            async with data_lock:
                store()
        else:
            store()
        return record if stored else {}

    async def _resolve_qweather_location(self) -> dict[str, Any]:
        """Resolve one shared location for current weather and official alerts."""

        config_key_before_read = self._qweather_location_cache_key()
        cached = self._qweather_cached_location(allow_stale=False)
        if cached and self._qweather_location_cache_key() == config_key_before_read:
            return cached
        lock = getattr(self, "_qweather_location_resolve_lock", None)
        if not isinstance(lock, asyncio.Lock):
            lock = asyncio.Lock()
            self._qweather_location_resolve_lock = lock
        async with lock:
            # A few rapid page saves can race with one request. Retrying a
            # small bounded number keeps the latest location responsive while
            # still falling back safely under continuous configuration churn.
            for _attempt in range(3):
                expected_config_key = self._qweather_location_cache_key()
                cached = self._qweather_cached_location(allow_stale=False)
                if cached and self._qweather_location_cache_key() == expected_config_key:
                    return cached
                stale = self._qweather_cached_location(allow_stale=True)
                direct = self._qweather_direct_location()
                configured = self._qweather_configured_location()
                if direct and (
                    not configured or self._qweather_coordinates_from_text(configured) is not None
                ):
                    stored = await self._store_qweather_location(
                        direct,
                        expected_config_key=expected_config_key,
                    )
                    if stored:
                        return stored
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    return {}
                if configured:
                    looked_up = await self._fetch_qweather_location_lookup(configured)
                    if self._qweather_location_cache_key() != expected_config_key:
                        continue
                    if looked_up:
                        stored = await self._store_qweather_location(
                            looked_up,
                            expected_config_key=expected_config_key,
                        )
                        if stored:
                            return stored
                        if self._qweather_location_cache_key() != expected_config_key:
                            continue
                        return {}
                    if stale:
                        return stale
                    # A LocationID remains valid for ordinary weather even
                    # when GeoAPI is temporarily unavailable. Alerts wait for
                    # its coordinates instead of guessing a different place.
                    if direct and direct.get("location_id"):
                        return direct
                    return {}
                return {}
            return {}
