"""Resolve a human-readable role location to QWeather coordinates."""

import json
import math

from companion_v2.agenda.planning_config import WeatherLocation

from .qweather_credentials import MAX_RESPONSE_BYTES, RESPONSE_LANGUAGE


ENDPOINT = "/geo/v2/city/lookup"


async def resolve_qweather_location(session, location, credentials, at):
    if type(location) is not WeatherLocation:
        raise ValueError("QWeather query requires an explicit role location")
    if location.latitude is not None and location.longitude is not None:
        return location

    params = {"location": location.name, "range": "cn", "number": 10,
              "lang": RESPONSE_LANGUAGE}
    if location.alert_city:
        params["adm"] = location.alert_city
    url = f"https://{credentials.host}{ENDPOINT}"
    async with session.get(url, params=params, headers=credentials.headers(at),
                           allow_redirects=False) as response:
        if (response.status != 200 or response.content_length is not None
                and response.content_length > MAX_RESPONSE_BYTES):
            raise ValueError("QWeather location lookup is unavailable")
        raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("QWeather location response exceeds the limit")

    payload = json.loads(raw)
    results = payload.get("location") if type(payload) is dict and payload.get("code") == "200" else None
    if type(results) is not list or not results:
        raise ValueError("QWeather location could not be resolved")
    for result in results:
        if type(result) is not dict:
            continue
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            return WeatherLocation(location.name, latitude, longitude,
                                   location.alert_city, location.alert_area)
    raise ValueError("QWeather location coordinates are invalid")
