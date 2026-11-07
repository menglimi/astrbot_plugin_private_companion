"""Zero-configuration fallback for China Meteorological Administration alerts.

QWeather remains the preferred alert source when the host has credentials.  An
installation that only configured a role location still needs to see the local
official warning, so this adapter reads NMC's public alarm index and maps the
matching city notice onto the same CAP-shaped observation used by the QWeather
port.  It never invents a warning: a title that does not name the configured
place produces an empty observation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import aiohttp

from companion_v2.agenda.planning_config import WeatherLocation
from companion_v2.proactive.weather_alerts import AlertObservation, OfficialWeatherAlert

ENDPOINT = "https://www.nmc.cn/rest/findAlarm"
MAX_RESPONSE_BYTES = 512 * 1024
CHINA_TZ = timezone(timedelta(hours=8))
_SEVERITY = (("红色", "extreme"), ("橙色", "severe"), ("黄色", "moderate"), ("蓝色", "minor"))
_ISSUED = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")


def _severity(title):
    for word, value in _SEVERITY:
        if word in title:
            return value
    return "unknown"


def _issued(value):
    match = _ISSUED.fullmatch(str(value or ""))
    if not match:
        raise ValueError("NMC issue time is invalid")
    return datetime(*(int(part) for part in match.groups()), tzinfo=CHINA_TZ)


def _keys(location):
    values = [location.name, location.alert_city, location.alert_area]
    keys = []
    for value in values:
        value = str(value or "").strip()
        if not value or value in keys:
            continue
        keys.append(value)
        if value.endswith(("市", "区", "县", "镇", "街道")):
            trimmed = value[:-1].strip()
            if trimmed and trimmed not in keys:
                keys.append(trimmed)
    return keys


def normalize_nmc_alerts(payload, location, queried_at):
    """Map the matching NMC alarm rows, keeping only titles for this place."""
    if type(location) is not WeatherLocation or queried_at.tzinfo is None:
        raise ValueError("NMC alert query requires a located, aware query")
    rows = (((payload or {}).get("data") or {}).get("page") or {}).get("list")
    if type(rows) is not list or len(rows) > 1000:
        raise ValueError("NMC alarm response is unavailable")
    keys = _keys(location)
    alerts = []
    for row in rows:
        if type(row) is not dict:
            continue
        title = row.get("title")
        identifier = row.get("alertid")
        if type(title) is not str or type(identifier) is not str or not identifier.strip():
            continue
        if not keys or not any(key in title for key in keys):
            continue
        issued = _issued(row.get("issuetime"))
        sender = title.split("发布", 1)[0].strip() or "中国气象局"
        alerts.append(OfficialWeatherAlert(
            identifier=identifier.strip(),
            headline=title.strip(),
            event_name=title.split("发布", 1)[-1].strip() or title.strip(),
            sender_name=sender,
            severity=_severity(title),
            message_type="alert",
            supersedes=(),
            issued_at=issued,
            effective_at=issued,
            expires_at=issued + timedelta(hours=12),
            description=title.strip(),
            instruction="",
        ))
    return AlertObservation(location, queried_at, ("中国气象局", "https://www.nmc.cn/"), tuple(alerts))


class NMCAlertPort:
    provider = "nmc"

    async def fetch(self, location):
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(ENDPOINT, params={"pageNo": 1, "pageSize": 500},
                                   headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                                   allow_redirects=False) as response:
                if (response.status != 200 or response.content_length is not None
                        and response.content_length > MAX_RESPONSE_BYTES):
                    raise ValueError("NMC alarms are unavailable")
                raw = await response.read()
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("NMC alarms exceed the limit")
        return normalize_nmc_alerts(json.loads(raw), location, datetime.now(CHINA_TZ))


def configured_nmc_alert_port():
    return NMCAlertPort()
