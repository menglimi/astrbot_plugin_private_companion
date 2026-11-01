"""QWeather current-weather adapter, the default source for the role's own location.

QWeather and Open-Meteo disagree on how they name a sky, so the mapping below is the
only place that difference is allowed to exist: it lands on the Open-Meteo/WMO code
scale that ``WeatherSnapshot.weather_code`` already uses, and every consumer keeps
working without knowing which provider answered.

The fetch is bounded the same way the alert adapter is -- fixed destination, short
timeout, no redirects, capped body -- because a provider misbehaving must not become a
runtime problem for the role.
"""

import asyncio
import json
from datetime import datetime, timezone

import aiohttp

from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.proactive.external_materials import parse_weather_response

from .qweather_credentials import (MAX_RESPONSE_BYTES, RESPONSE_LANGUAGE,
                                   credentials_from_environment)
from .qweather_location import resolve_qweather_location


ENDPOINT = "/v7/weather/now"

# The callers bound a whole weather read at 6-7s (companion_v2/agenda/weather_reading.py,
# weather_tools.py), while a name-only role location costs two requests: the geo lookup
# and the observation. Two per-request timeouts could therefore always overshoot the
# caller's budget twice over on a slow network, so the adapter spends one total budget
# that both requests share.
TOTAL_TIMEOUT_SECONDS = 5

# QWeather "now" icon codes -> WMO codes. Grouped by the icon's hundreds digit, which
# is what QWeather's own icon set is organised by (1xx clear, 3xx rain, 4xx snow...),
# so a code this table has not seen still lands in the right family instead of failing
# the whole observation.
_WMO_BY_QWEATHER_ICON = {
    100: 0, 150: 0, 101: 2, 102: 2, 103: 1, 104: 3, 151: 2, 152: 2, 153: 1, 154: 3,
    300: 80, 301: 82, 302: 95, 303: 95, 304: 96, 305: 61, 306: 63, 307: 65,
    308: 65, 309: 51, 310: 65, 311: 65, 312: 65, 313: 66, 314: 61, 315: 63,
    316: 65, 317: 65, 318: 65, 350: 80, 351: 82, 399: 61,
    400: 71, 401: 73, 402: 75, 403: 75, 404: 66, 405: 66, 406: 66, 407: 71,
    408: 73, 409: 75, 410: 75, 456: 85, 457: 86, 499: 73,
    # 500-515 are mist, fog, haze, sand and dust. WMO only has 45/48 for fog and no
    # code at all for haze or dust, so the whole family lands on 45: it is the least
    # wrong sky for the consumers, and 48 stays reserved for rime fog (QWeather's icon
    # set has no rime icon, so nothing maps to 48). 501 is fog proper, not rime, which
    # is what it used to claim.
    500: 45, 501: 45, 502: 45, 503: 45, 504: 45, 507: 45, 508: 45, 509: 45,
    510: 45, 511: 45, 512: 45, 513: 45, 514: 45, 515: 45,
    # 900/901 are temperature extremes ("热"/"冷"), not sky phenomena. Mapping them to
    # the thunder branch made a hot afternoon look like a thunderstorm. Any dry code
    # is really arbitrary here; 0 and 3 keep the change detector from reporting a sky
    # change while the provider's own text remains the visible condition.
    900: 0, 901: 3,
    # 999 means "unknown". It used to publish an unknown sky as clear (0), which both
    # displayed the wrong condition and could trigger the weather-change contact. No
    # code is the honest answer; with no text label the observation then fails closed.
    999: None,
}
_FAMILY_FALLBACK = {1: 2, 2: 2, 3: 80, 4: 73, 5: 45}
# Icons whose WMO code cannot name them on its own (haze and dust have no WMO sky
# code) or which are not sky states at all (temperature extremes, "unknown"). A blank
# text label there must fail instead of inventing a sky from the mapped code.
_NO_SKY_LABEL_ICONS = frozenset({502, 503, 504, 507, 508, 900, 901, 999})

# Only reached when QWeather sends an observation with no text label, which would
# otherwise fail the shared parser and discard a reading the icon already describes.
_LABEL_BY_WMO = {
    0: "晴", 1: "多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 71: "小雪",
    73: "中雪", 75: "大雪", 80: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹",
}


def _icon_number(icon):
    """The icon as an integer when it is one, else ``None`` (the API sends strings)."""
    if type(icon) is str and icon.isdigit():
        return int(icon)
    return icon if type(icon) is int else None


def weather_code_for(icon):
    """Map a QWeather icon code onto the WMO scale, or ``None`` when unusable."""
    icon = _icon_number(icon)
    if icon is None or icon < 0:
        return None
    if icon in _WMO_BY_QWEATHER_ICON:
        return _WMO_BY_QWEATHER_ICON[icon]
    return _FAMILY_FALLBACK.get(icon // 100)


def _number(value):
    if type(value) in (int, float):
        return value
    if type(value) is str:
        try:
            return float(value)
        except ValueError:
            return None
    return None


def normalize_qweather_now(payload, location):
    """Map a QWeather ``/v7/weather/now`` body onto the provider-neutral envelope."""
    if type(payload) is not dict or payload.get("code") != "200":
        raise ValueError("QWeather current weather is unavailable")
    now = payload.get("now")
    if type(now) is not dict:
        raise ValueError("QWeather current weather is unavailable")
    temperature = _number(now.get("temp"))
    if temperature is None:
        raise ValueError("QWeather temperature is unavailable")
    icon = _icon_number(now.get("icon"))
    code = weather_code_for(icon)
    condition = now.get("text")
    if type(condition) is not str:
        condition = ""
    condition = condition.strip()
    if not condition:
        # The icon already names the sky. Losing a whole observation over a missing
        # label would be the tail wagging the dog. Icons that are not sky states (see
        # _NO_SKY_LABEL_ICONS) cannot do that, so there a missing label stays a failure.
        if icon in _NO_SKY_LABEL_ICONS:
            raise ValueError("QWeather condition is unavailable")
        condition = _LABEL_BY_WMO.get(code, "")
    if not condition:
        raise ValueError("QWeather condition is unavailable")
    observed = now.get("obsTime")
    if type(observed) is not str or not observed.strip():
        raise ValueError("QWeather observation time is unavailable")
    try:
        timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("QWeather observation time is invalid") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("QWeather observation time must be timezone aware")
    envelope = {
        "provider": "qweather",
        "location": location.name,
        "observed_at": timestamp.isoformat(),
        "current": {
            "temperature_c": temperature,
            "condition": condition,
            "humidity_pct": _number(now.get("humidity")),
            "wind_kph": _number(now.get("windSpeed")),
        },
    }
    if code is not None:
        envelope["current"]["weather_code"] = code
    return parse_weather_response(envelope)[0]


class QWeatherWeatherPort:
    provider = "qweather"

    def __init__(self, credentials, *, now=None):
        self._credentials = credentials
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def fetch(self, location):
        if type(location) is not WeatherLocation:
            raise ValueError("QWeather weather query requires an explicit role location")
        at = self._now()
        try:
            return await asyncio.wait_for(self._fetch(location, at), TOTAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as error:
            # Reported like every other unavailable response, so the callers' existing
            # fail-closed path applies instead of a provider-specific exception.
            raise ValueError("QWeather current weather timed out") from error

    async def _fetch(self, location, at):
        timeout = aiohttp.ClientTimeout(total=TOTAL_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            location = await resolve_qweather_location(session, location, self._credentials, at)
            url = f"https://{self._credentials.host}{ENDPOINT}"
            params = {"location": f"{location.longitude:.2f},{location.latitude:.2f}",
                      "lang": RESPONSE_LANGUAGE}
            async with session.get(url, params=params, headers=self._credentials.headers(at),
                                  allow_redirects=False) as response:
                if (response.status != 200 or response.content_length is not None
                        and response.content_length > MAX_RESPONSE_BYTES):
                    raise ValueError("QWeather weather response is unavailable")
                raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("QWeather weather response exceeds the limit")
        return normalize_qweather_now(json.loads(raw), location)


def configured_qweather_weather_port(config=None):
    """Prefer QWeather when host credentials exist, else fall back to Open-Meteo.

    QWeather is the default because it is the only source that can also serve official
    alerts, so one credential covers both. Open-Meteo stays the zero-configuration
    path for an installation that never set any credential.
    """
    settings = config.get("weather", {}) if isinstance(config, dict) else {}
    provider = settings.get("provider", "auto")
    if provider not in ("auto", "qweather", "open-meteo"):
        raise ValueError("invalid weather provider")
    if provider == "open-meteo":
        from .weather import OpenMeteoWeatherPort
        return OpenMeteoWeatherPort()
    credentials = credentials_from_environment(label="host", config=config)
    if credentials is None:
        if provider == "qweather":
            raise ValueError("QWeather requires host credentials")
        from .weather import OpenMeteoWeatherPort
        return OpenMeteoWeatherPort()
    return QWeatherWeatherPort(credentials)
