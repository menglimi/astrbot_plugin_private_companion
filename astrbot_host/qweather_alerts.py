"""Bounded QWeather official-alert adapter using host-only credentials."""

import json
from datetime import datetime, timezone

import aiohttp

from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.proactive.weather_alerts import normalize_qweather_alerts

from .qweather_credentials import (MAX_RESPONSE_BYTES, RESPONSE_LANGUAGE,
                                   credentials_from_environment)
from .qweather_location import resolve_qweather_location


class QWeatherAlertPort:
    def __init__(self, credentials, *, now=None):
        self._credentials = credentials
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def fetch(self, location):
        if type(location) is not WeatherLocation:
            raise ValueError("QWeather alert query requires an explicit role location")
        at = self._now()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("QWeather query clock must be timezone aware")
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            query = await resolve_qweather_location(session, location, self._credentials, at)
            url = (f"https://{self._credentials.host}/weatheralert/v1/current/"
                   f"{query.latitude:.2f}/{query.longitude:.2f}")
            async with session.get(url, params={"lang": RESPONSE_LANGUAGE},
                                  headers=self._credentials.headers(at),
                                  allow_redirects=False) as response:
                if (response.status != 200 or response.content_length is not None
                        and response.content_length > MAX_RESPONSE_BYTES):
                    raise ValueError("QWeather alert response is unavailable")
                raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("QWeather alert response exceeds the limit")
        return normalize_qweather_alerts(json.loads(raw), location, self._now())


def configured_qweather_alert_port(config=None):
    """Read optional host-only credentials; never persist them in role configuration."""
    credentials = credentials_from_environment(label="alert", config=config)
    return QWeatherAlertPort(credentials) if credentials is not None else None
