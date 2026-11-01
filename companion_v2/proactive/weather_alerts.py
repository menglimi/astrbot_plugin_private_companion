"""Official weather-alert observations, separate from current-weather codes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from companion_v2.agenda.planning_config import WeatherLocation


MAX_OBSERVATION_AGE = timedelta(minutes=10)
FUTURE_TOLERANCE = timedelta(minutes=5)
SEVERITIES = frozenset({"unknown", "minor", "moderate", "severe", "extreme"})
SEVERITY_RANK = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
MESSAGE_TYPES = frozenset({"alert", "update", "cancel"})


@dataclass(frozen=True)
class OfficialWeatherAlert:
    identifier: str
    headline: str
    event_name: str
    sender_name: str
    severity: str
    message_type: str
    supersedes: tuple[str, ...]
    issued_at: datetime
    effective_at: datetime
    expires_at: datetime
    description: str
    instruction: str


@dataclass(frozen=True)
class AlertObservation:
    location: WeatherLocation
    queried_at: datetime
    attributions: tuple[str, ...]
    alerts: tuple[OfficialWeatherAlert, ...]


def _text(value, name, *, optional=False, maximum=8192):
    if value is None and optional:
        return ""
    if type(value) is not str or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError(name)
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError(name)
    return value


def _time(value, name):
    if type(value) is not str:
        raise ValueError(name)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(name) from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(name)
    return result


def _alert(row):
    if not isinstance(row, Mapping):
        raise ValueError("alert")
    message = row.get("messageType")
    event = row.get("eventType")
    if not isinstance(message, Mapping) or not isinstance(event, Mapping):
        raise ValueError("alert.type")
    kind = _text(message.get("code"), "alert.messageType", maximum=16)
    severity = _text(row.get("severity"), "alert.severity", maximum=16).lower()
    supersedes = message.get("supersedes")
    if (kind not in MESSAGE_TYPES or severity not in SEVERITIES or type(supersedes) is not list
            or len(supersedes) > 32):
        raise ValueError("alert.classification")
    replaced = tuple(_text(item, "alert.supersedes", maximum=128) for item in supersedes)
    issued = _time(row.get("issuedTime"), "alert.issuedTime")
    effective = _time(row.get("effectiveTime"), "alert.effectiveTime")
    expires = _time(row.get("expireTime"), "alert.expireTime")
    if expires <= effective:
        raise ValueError("alert.range")
    return OfficialWeatherAlert(
        _text(row.get("id"), "alert.id", maximum=128),
        _text(row.get("headline"), "alert.headline", maximum=1024),
        _text(event.get("name"), "alert.eventType", maximum=128),
        _text(row.get("senderName"), "alert.senderName", optional=True, maximum=128),
        severity, kind, replaced, issued, effective, expires,
        _text(row.get("description"), "alert.description", optional=True),
        _text(row.get("instruction"), "alert.instruction", optional=True),
    )


def normalize_qweather_alerts(payload, location, queried_at):
    """Validate the current QWeather alert response without inventing warning status."""
    if type(location) is not WeatherLocation or queried_at.tzinfo is None:
        raise ValueError("alert observation requires a located, aware query")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("metadata"), Mapping):
        raise ValueError("alert response")
    metadata, rows = payload["metadata"], payload.get("alerts")
    attributions = metadata.get("attributions")
    if (type(metadata.get("zeroResult")) is not bool or type(rows) is not list or len(rows) > 32):
        raise ValueError("alert response fields")
    if metadata["zeroResult"] and rows:
        raise ValueError("alert zeroResult conflicts with alerts")
    if attributions is None and metadata["zeroResult"] and not rows:
        attributions = []
    if (type(attributions) is not list or len(attributions) > 16
            or (rows and not attributions)):
        raise ValueError("alert response fields")
    attribution = tuple(_text(item, "alert.attribution", maximum=1024) for item in attributions)
    alerts = tuple(_alert(row) for row in rows)
    if len({alert.identifier for alert in alerts}) != len(alerts):
        raise ValueError("duplicate alert ids")
    return AlertObservation(location, queried_at, attribution, alerts)


def active_official_alerts(observation, location, at):
    """A canceled, stale or relocated observation cannot create a contact cause."""
    if (type(observation) is not AlertObservation or observation.location != location
            or at.tzinfo is None or not -FUTURE_TOLERANCE <= at - observation.queried_at <= MAX_OBSERVATION_AGE):
        return ()
    # 只有已经生效且尚未过期的 update/cancel 才能作废旧预警。接口可能在同一次
    # 响应里给出生效时间在未来的更新（或一条早已过期的更新行）；旧实现无条件
    # 收集 supersedes，会把仍在有效期内的原预警提前作废，导致这段空窗期静默漏报。
    canceled = {identifier for item in observation.alerts if item.message_type in {"update", "cancel"}
                and item.effective_at <= at < item.expires_at
                for identifier in item.supersedes}
    return tuple(item for item in observation.alerts
                 if item.message_type != "cancel" and item.identifier not in canceled
                 and item.issued_at <= at + FUTURE_TOLERANCE
                 and item.effective_at <= at < item.expires_at)
