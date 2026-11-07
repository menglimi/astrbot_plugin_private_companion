"""Convert the V1 weather configuration into V2 installation, role and contact settings.

V1 stored weather as a flat group of 24 keys: a source selector, five location fields,
credentials, an alert family, and five "link it to a proactive message" switches. V2
splits the same concern across three scopes: source and cache settings live in the
installation's plugin configuration, role observation settings in ``role.state``, and
contact behaviour in ``proactive.contact``. Host credentials are applied through the
V2 host-only weather settings service and never enter role settings or migration receipts.

Two of V1's settings cannot survive the move unchanged, and silently translating them
would leave an operator with a bot that behaves differently for reasons they cannot see:

* the refresh interval has a structural 120-minute ceiling in V2,
* the environment-change cooldown becomes a de-duplication window.

So this module returns both the converted values and a list of differences. The caller
is expected to show the differences rather than swallow them.

Nothing here reads a file or the environment: the function is a pure transformation of a
mapping, which is what lets it be tested with plain dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

from companion_v2.agenda.planning_config import WEATHER_MAX_AGE_MINUTES, WEATHER_MAX_AGE_MINIMUM
from companion_v2.foundation.outcome import Outcome


STATE = "role.state"
CONTACT = "proactive.contact"

# V2 reads the location as one JSON document, so these three V1 keys collapse into it.
_LOCATION_NAME_KEYS = ("weather_city", "weather_location")
# A QWeather LocationID is an opaque number, not a place name. Using it as the label
# would show the operator a bare number in the interface, so it is treated as absent
# and reported instead of being smuggled through.
_LOCATION_ID = re.compile(r"^\d{5,12}$")

# V1 filters alerts by the four colour tiers Chinese weather warnings use; V2 uses the
# CAP five-tier scale. Both are ordered scales of the same shape, so the colours map
# rank-for-rank: blue is the lowest tier V1 could select and ``minor`` is V2's lowest,
# ``red`` is V1's top and ``extreme`` is V2's. The words themselves share no
# vocabulary, so passing one through would be rejected by the severity validator.
_SEVERITY_BY_COLOUR = {
    "blue": "minor", "蓝色": "minor",
    "yellow": "moderate", "黄色": "moderate",
    "orange": "severe", "橙色": "severe",
    "red": "extreme", "红色": "extreme",
}
# V1's "all" meant "do not filter"; V2's empty string means the same thing.
_SEVERITY_ALL = ("all", "全部", "")


def severity_for_colour(value: str) -> str:
    """Map a V1 colour tier onto the V2 severity scale, or ``""`` for no filter."""
    folded = value.strip().lower()
    if folded in _SEVERITY_ALL:
        return ""
    return _SEVERITY_BY_COLOUR.get(folded, "")


@dataclass(frozen=True)
class Difference:
    """One setting whose V2 meaning is not the V1 meaning."""

    field_id: str
    kind: str
    summary: str
    v1_value: str = ""
    v2_value: str = ""


@dataclass(frozen=True)
class WeatherMigration:
    """The V2 changes to apply, plus what the operator must be told about them."""

    changes: dict[str, object]
    differences: tuple[Difference, ...]
    skipped: tuple[str, ...]

    def document(self) -> dict:
        return {
            "changes": {key: self.changes[key] for key in sorted(self.changes)},
            "differences": [
                {"field_id": item.field_id, "kind": item.kind, "summary": item.summary,
                 "v1_value": item.v1_value, "v2_value": item.v2_value}
                for item in self.differences
            ],
            "skipped": list(self.skipped),
        }


def _text(value: object) -> str:
    """V1 wrote mostly strings, but also numbers for coordinates and IDs."""
    if value is None:
        return ""
    return value.strip() if type(value) is str else str(value).strip()


def _flag(value: object, default: bool = False) -> bool:
    """V1 wrote booleans as booleans, but also as 'true'/'false' strings."""
    if type(value) is bool:
        return value
    if type(value) is str:
        folded = value.strip().lower()
        if folded in ("true", "yes", "1", "on"):
            return True
        if folded in ("false", "no", "0", "off", ""):
            return False
    if type(value) is int:
        return bool(value)
    return default


def _minutes(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _clamp(value: int, minimum: int, maximum: int) -> int:
    """Keep a converted value inside the V2 field's declared range.

    Out-of-range values are rejected at save time, so a converter that passes one
    through produces a config write that fails for a reason the operator cannot see.
    """
    return max(minimum, min(maximum, value))


def _location(config) -> tuple[str, list[Difference], list[str]]:
    """Build the V2 location document, or explain why it could not be built."""
    differences: list[Difference] = []
    skipped: list[str] = []
    name = next((_text(config.get(key)) for key in _LOCATION_NAME_KEYS
                 if _text(config.get(key)) and not _LOCATION_ID.match(_text(config.get(key)))), "")
    numeric_id = next((_text(config.get(key)) for key in _LOCATION_NAME_KEYS
                       if _LOCATION_ID.match(_text(config.get(key)))), "")
    if numeric_id and not name:
        skipped.append("weather_location")
        differences.append(Difference(
            "role.state.planning_weather_location", "needs_input",
            "V1 填的是和风 LocationID（一串数字），不是地名。V2 的地点标签要用可读名称，"
            "请重新填写地点名称与经纬度。", numeric_id, ""))
    try:
        latitude = float(config.get("weather_lat") or 0)
        longitude = float(config.get("weather_lon") or 0)
    except (TypeError, ValueError):
        latitude = longitude = 0.0
    if not name or not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180) or (latitude == 0 and longitude == 0):
        if "weather_location" not in skipped:
            skipped.append("weather_location")
            differences.append(Difference(
                "role.state.planning_weather_location", "needs_input",
                "V1 没有可用的地点名称与经纬度，V2 需要显式地点才能启用天气观测。", "", ""))
        return "", differences, skipped
    document = {"name": name[:64], "latitude": latitude, "longitude": longitude,
                # V1's alert lane was city-level; keep that intent so migrated
                # installations do not silently lose official warnings just
                # because V2 now has explicit alert city/area fields.
                "alert_city": name[:64], "alert_area": name[:64]}
    return json.dumps(document, ensure_ascii=False), differences, skipped


def convert_weather_config(config) -> Outcome[WeatherMigration]:
    """Map a V1 weather configuration onto V2 settings.

    ``config`` is the V1 ``weather_config`` mapping (or the legacy flat keys). Unknown
    keys are ignored rather than rejected: V1 grew keys over several releases and a
    migration must not fail because an installation carries one this build predates.
    """
    if not isinstance(config, dict):
        return Outcome.failed("weather_migration_invalid", "V1 天气配置必须是键值映射")

    changes: dict[str, object] = {}
    differences: list[Difference] = []
    skipped: list[str] = []

    location, location_differences, location_skipped = _location(config)
    differences.extend(location_differences)
    skipped.extend(location_skipped)

    enabled = _flag(config.get("enable_weather_context"), True)
    if location:
        changes[STATE + ".planning_weather_location"] = location
    if enabled and not location:
        # V1 defaulted this to on; carrying that across without a location would leave
        # the setting enabled and permanently unsatisfied.
        enabled = False
        differences.append(Difference(
            STATE + ".planning_weather_enabled", "downgraded",
            "V1 的天气上下文默认开启，但 V2 没有地点就无法观测，已先关闭；填好地点后可再开启。",
            "true", "false"))
    changes[STATE + ".planning_weather_enabled"] = enabled

    requested = _minutes(config.get("weather_refresh_minutes"), 90)
    if requested < WEATHER_MAX_AGE_MINIMUM:
        requested = WEATHER_MAX_AGE_MINIMUM
    if requested > WEATHER_MAX_AGE_MINUTES:
        changes[STATE + ".planning_weather_max_age_minutes"] = WEATHER_MAX_AGE_MINUTES
        differences.append(Difference(
            STATE + ".planning_weather_max_age_minutes", "narrowed",
            f"V2 的观测新鲜度上限是 {WEATHER_MAX_AGE_MINUTES} 分钟（匹配「一次当前天气观测」"
            f"的语义），V1 允许更长。刷新会比原来频繁。",
            str(requested), str(WEATHER_MAX_AGE_MINUTES)))
    else:
        changes[STATE + ".planning_weather_max_age_minutes"] = requested

    changes[CONTACT + ".official_weather_alert_enabled"] = _flag(config.get("enable_weather_alerts"), False)
    changes[CONTACT + ".official_alert_poll_minutes"] = _clamp(
        _minutes(config.get("weather_alert_refresh_minutes"), 10), 1, 1440)
    raw_severity = _text(config.get("weather_alert_min_severity"))
    if raw_severity:
        severity = severity_for_colour(raw_severity)
        changes[CONTACT + ".official_alert_min_severity"] = severity
        if not severity:
            differences.append(Difference(
                CONTACT + ".official_alert_min_severity", "unknown_value",
                "V1 的预警级别是四色（blue/yellow/orange/red）或 all；V2 用 CAP 五级"
                "（minor/moderate/severe/extreme），已按等级对应关系转换。这个值不在已知范围内，"
                "已置为「不过滤」，请重新选择。", raw_severity, ""))
        elif severity.lower() != raw_severity.lower():
            differences.append(Difference(
                CONTACT + ".official_alert_min_severity", "renamed",
                "V1 的四色预警级别在 V2 用 CAP 等级表示，已按等级对应："
                "blue→minor、yellow→moderate、orange→severe、red→extreme；V1 的 all 对应 V2 的空值（不过滤）。",
                raw_severity, severity))

    # V1's comfort switch became two independent V2 settings: how often to sample, and
    # whether a change is worth speaking about.
    changes[CONTACT + ".weather_sample_minutes"] = _clamp(
        _minutes(config.get("environment_change_check_minutes"), 60), 10, 1440)
    change_enabled = _flag(config.get("enable_environment_change_proactive"), False)
    changes[CONTACT + ".weather_change_enabled"] = change_enabled

    cooldown = _minutes(config.get("environment_change_cooldown_minutes"), 0)
    if cooldown:
        window = _clamp(cooldown, 0, 2880)
        changes[CONTACT + ".dedup_weather_window_minutes"] = window
        differences.append(Difference(
            CONTACT + ".dedup_weather_window_minutes", "resemanticised",
            "V1 的「环境突变消息冷却」是「说完这句多久内不再说」；V2 的是「同样内容多久内不重复」，"
            "按原数值迁入。默认的去重策略已包含 event_revision，因此天气突变与官方预警都会受此窗口抑制。",
            str(cooldown), str(window)))

    # Host credentials have dedicated plugin-config rules and are applied by the host
    # weather settings service. Keep only fields that have no V2 migration destination.
    for key, why, secret in (
        ("weather_api_key", "V2 不再使用 OpenWeatherMap 源，该专用密钥没有对应设置。", True),
        ("weather_amap_api_key", "V2 没有高德数据源。", True),
        ("weather_amap_city", "V2 没有高德数据源。", False),
        ("weather_alert_api_host", "V1 兼容别名；生效 Host 由 weather_api_host 映射。", True),
        ("weather_alert_token", "V1 兼容别名；生效凭据由 weather_token 映射。", True),
        # V1 允许用这个键代替 weather_alert_token 存预警凭据（plugin_bootstrap 把它
        # 当作预警令牌的兼容来源），漏报它就等于悄悄丢一份凭据。
        ("weather_alert_api_key", "凭据改为从环境变量读取。", True),
        ("mobile_location_weather_sensitivity",
         "V2 的天气绑定角色设置的固定地点，不跟随移动位置，没有这项。", False),
    ):
        if key in config and _text(config.get(key)):
            skipped.append(key)
            differences.append(Difference(key, "not_migrated", why,
                                          "（已填写，值不在此显示）" if secret else _text(config.get(key)),
                                          ""))

    return Outcome.success(WeatherMigration(
        changes=changes,
        differences=tuple(differences),
        skipped=tuple(dict.fromkeys(skipped))))
