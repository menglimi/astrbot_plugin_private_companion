"""Planning preferences exposed through the existing calendar management record."""

import json
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_v2.configuration.models import ConfigurationError
from .annual_calendar import parse_annual_calendar


# The structural freshness ceiling lives here because weather_material imports this
# module, not the other way around. An operator setting may only narrow inside it.
WEATHER_MAX_AGE_MINUTES = 120
WEATHER_MAX_AGE_MINIMUM = 5
SCHEDULE_MODE_CONTINUOUS = "continuous"
SCHEDULE_MODE_ORCHESTRATED = "orchestrated"
SCHEDULE_MODES = (SCHEDULE_MODE_CONTINUOUS, SCHEDULE_MODE_ORCHESTRATED)

DEFAULTS = {
    "schedule_simulation_mode": SCHEDULE_MODE_ORCHESTRATED,
    "planning_enabled": False,
    "planning_timezone": "Asia/Shanghai",
    "planning_horizon_days": 90,
    "planning_refine_minutes": 15,
    "planning_max_items": 32,
    "planning_prompt": "",
    "planning_annual_calendars": "",
    "planning_weather_enabled": False,
    "planning_weather_location": "",
    "planning_weather_max_age_minutes": WEATHER_MAX_AGE_MINUTES,
}
MAXIMUMS = {"planning_horizon_days": 366, "planning_refine_minutes": 1440, "planning_max_items": 32,
            "planning_weather_max_age_minutes": WEATHER_MAX_AGE_MINUTES}


@dataclass(frozen=True)
class WeatherLocation:
    name: str
    latitude: float
    longitude: float
    alert_city: str = ""
    alert_area: str = ""


def configured_weather_location(value):
    if type(value) is not str or len(value.encode("utf-8")) > 512:
        raise ConfigurationError("planning_weather_location_invalid", "provide a short explicit role location")
    if not value.strip():
        return None
    try:
        row = json.loads(value)
        if type(row) is not dict or "name" not in row or (
                set(row) - {"name", "latitude", "longitude", "alert_city", "alert_area"}):
            raise ValueError("location fields")
        name = row["name"]
        latitude, longitude = row.get("latitude"), row.get("longitude")
        alert_city, alert_area = row.get("alert_city", ""), row.get("alert_area", "")
        if (type(name) is not str or not 1 <= len(name.strip()) <= 64
                or any(ord(character) < 32 for character in name)):
            raise ValueError("location name")
        if (latitude is None) != (longitude is None):
            raise ValueError("coordinate pair")
        if latitude is not None and (type(latitude) not in (int, float) or type(longitude) not in (int, float)
                                     or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            raise ValueError("location coordinates")
        if any(type(item) is not str or len(item.strip()) > 64 or any(ord(c) < 32 for c in item)
               for item in (alert_city, alert_area)) or bool(alert_city.strip()) != bool(alert_area.strip()):
            raise ValueError("alert area")
        return WeatherLocation(name.strip(), float(latitude) if latitude is not None else None,
                               float(longitude) if longitude is not None else None,
                               alert_city.strip(), alert_area.strip())
    except (TypeError, ValueError, OverflowError, UnicodeError) as error:
        raise ConfigurationError("planning_weather_location_invalid", "provide a name, latitude and longitude") from error


def configured_annual_calendars(value):
    if type(value) is not str:
        raise ConfigurationError("planning_annual_calendars_invalid", "provide at most 16 KiB of annual calendar JSON")
    try:
        if len(value.encode("utf-8")) > 16384:
            raise ValueError("annual calendar JSON exceeds 16 KiB")
        if not value.strip():
            return ()
        rows = json.loads(value)
        if type(rows) is not list or not 1 <= len(rows) <= 3:
            raise ValueError("provide one to three annual calendars")
        calendars = tuple(parse_annual_calendar(row) for row in rows)
        if len({item.year for item in calendars}) != len(calendars):
            raise ValueError("annual calendar years must be unique")
        for calendar in calendars:
            days = {day for day, _ in (*calendar.holiday_days, *calendar.makeup_workdays, *calendar.solar_terms)}
            names = (name for _, name in (*calendar.holiday_days, *calendar.makeup_workdays, *calendar.solar_terms))
            if len(days) > 96 or any(len(name) > 64 for name in names):
                raise ValueError("annual calendar entries exceed the planning limit")
        return calendars
    except (TypeError, ValueError, OverflowError, UnicodeError) as error:
        raise ConfigurationError("planning_annual_calendars_invalid", "annual calendar JSON is invalid") from error


def validate_settings(values):
    mode = values.get("schedule_simulation_mode", SCHEDULE_MODE_ORCHESTRATED)
    if mode not in SCHEDULE_MODES:
        raise ConfigurationError("schedule_simulation_mode_invalid", "choose a supported schedule simulation mode")
    try:
        ZoneInfo(values["planning_timezone"])
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ConfigurationError("planning_timezone_invalid", "use an IANA planning timezone") from error
    if len(values["planning_prompt"]) > 4096:
        raise ConfigurationError("planning_prompt_invalid", "keep planning preferences within 4096 characters")
    configured_annual_calendars(values["planning_annual_calendars"])
    location = configured_weather_location(values["planning_weather_location"])
    if values["planning_weather_enabled"] and location is None:
        raise ConfigurationError("planning_weather_location_required", "set the role location before enabling weather")
    freshness = values["planning_weather_max_age_minutes"]
    if (type(freshness) is not int or isinstance(freshness, bool)
            or not WEATHER_MAX_AGE_MINIMUM <= freshness <= WEATHER_MAX_AGE_MINUTES):
        raise ConfigurationError("planning_weather_max_age_invalid",
            f"keep the weather freshness window between {WEATHER_MAX_AGE_MINIMUM} and {WEATHER_MAX_AGE_MINUTES} minutes")


def configured_weather_max_age(values):
    """The effective freshness window, always inside the structural ceiling."""
    freshness = values.get("planning_weather_max_age_minutes", WEATHER_MAX_AGE_MINUTES)
    if (type(freshness) is not int or isinstance(freshness, bool)
            or not WEATHER_MAX_AGE_MINIMUM <= freshness <= WEATHER_MAX_AGE_MINUTES):
        return timedelta(minutes=WEATHER_MAX_AGE_MINUTES)
    return timedelta(minutes=freshness)


def planning_values(values):
    return {name: values[name] for name in (*DEFAULTS, "calendar_enabled")}
