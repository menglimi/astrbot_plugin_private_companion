"""Resolve a human-readable role location to QWeather coordinates."""

import json
import math

from companion_v2.agenda.planning_config import WeatherLocation

from .qweather_credentials import MAX_RESPONSE_BYTES, RESPONSE_LANGUAGE


ENDPOINT = "/geo/v2/city/lookup"

# The lookup is global (see below), so a name that also exists abroad could otherwise
# win on relevance alone. The request pins the API's language to Chinese (``lang``),
# which makes ``country`` the stable domestic marker used here. The preference only orders
# candidates: a role abroad whose name exists nowhere in China (维也纳) still resolves,
# and a genuinely ambiguous one can be pinned with the location's alert city.
_HOME_COUNTRY = "中国"


def _match_order(result, name):
    """Exact spelling first, then domestic, then QWeather's own relevance order."""
    return (0 if result.get("name") == name else 1,
            0 if result.get("country") == _HOME_COUNTRY else 1)


async def resolve_qweather_location(session, location, credentials, at):
    if type(location) is not WeatherLocation:
        raise ValueError("QWeather query requires an explicit role location")
    if location.latitude is not None and location.longitude is not None:
        return location

    # ``range`` limits the search to one country. It used to be pinned to ``cn``, which
    # made a role outside mainland China impossible to geocode: the whole weather chain
    # then reported itself unavailable even though the credentials and the name were
    # fine. The lookup is left worldwide; ``adm`` still narrows it when the role has a
    # configured city, and the ordering below keeps the familiar domestic place first
    # when a name repeats.
    params = {"location": location.name, "number": 10, "lang": RESPONSE_LANGUAGE}
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
    for result in sorted((item for item in results if type(item) is dict),
                         key=lambda item: _match_order(item, location.name)):
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (math.isfinite(latitude) and math.isfinite(longitude)
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            return WeatherLocation(location.name, latitude, longitude,
                                   location.alert_city, location.alert_area)
    raise ValueError("QWeather location coordinates are invalid")
