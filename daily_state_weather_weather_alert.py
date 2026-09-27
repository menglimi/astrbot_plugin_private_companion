# -*- coding: utf-8 -*-
"""DailyStateWeatherWeatherAlertMixin。

由 tools/split_mixin_domain.py 从 daily_state_weather.py 机械抽取（17 个方法 + 0 个模块级名字 + 0 个类级赋值 / 559 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 DailyStateWeatherMixin）。
"""
from __future__ import annotations

from .daily_state_weather_shared import (
    _now_ts,
    _qweather_alert_color,
    _qweather_alert_first,
    _qweather_alert_rank,
    _qweather_alert_string_list,
    _qweather_alert_text,
    logger,
)
from .daily_state_weather_shared import Any
from .daily_state_weather_shared import _safe_float
from .daily_state_weather_shared import _safe_int
from .daily_state_weather_shared import _single_line
from .daily_state_weather_shared import asyncio
from .daily_state_weather_shared import deepcopy
from .daily_state_weather_shared import hashlib
from .daily_state_weather_shared import runtime_persona_setting
from .daily_state_weather_shared import urlencode



class DailyStateWeatherWeatherAlertMixin:
    """DailyStateWeatherWeatherAlertMixin（从 DailyStateWeatherMixin 拆出）。"""


    def _qweather_alert_location(self, resolved: Any = None) -> tuple[float, float] | None:
        """Return (latitude, longitude), preferring dedicated alert fields."""

        if isinstance(resolved, dict):
            latitude = self._qweather_alert_coordinate(resolved.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(resolved.get("lon"), minimum=-180, maximum=180)
            if latitude is not None and longitude is not None and (latitude != 0 or longitude != 0):
                return latitude, longitude
        if self._qweather_configured_location():
            snapshot = self._qweather_location_snapshot()
            latitude = self._qweather_alert_coordinate(snapshot.get("lat"), minimum=-90, maximum=90)
            longitude = self._qweather_alert_coordinate(snapshot.get("lon"), minimum=-180, maximum=180)
            if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
                return None
            return latitude, longitude

        lat_value = getattr(self, "weather_alert_lat", None)
        lon_value = getattr(self, "weather_alert_lon", None)
        if lat_value in (None, "") or lon_value in (None, ""):
            lat_value = runtime_persona_setting(self, "weather_lat", lat_value)
            lon_value = runtime_persona_setting(self, "weather_lon", lon_value)
        lat = self._qweather_alert_coordinate(lat_value, minimum=-90, maximum=90)
        lon = self._qweather_alert_coordinate(lon_value, minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            # A few callers keep a location in a single "lat,lon" setting.
            raw_location = str(getattr(self, "weather_alert_location", "") or "").strip()
            pieces = [part.strip() for part in raw_location.split(",")]
            if len(pieces) == 2:
                lat = self._qweather_alert_coordinate(pieces[0], minimum=-90, maximum=90)
                lon = self._qweather_alert_coordinate(pieces[1], minimum=-180, maximum=180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            return None
        return lat, lon

    @staticmethod
    def _qweather_alert_coordinate_text(value: float, *, decimals: int = 6) -> str:
        try:
            precision = max(0, min(6, int(decimals)))
        except (TypeError, ValueError):
            precision = 6
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def _build_qweather_alert_url(self, resolved: Any = None) -> str:
        host = self._qweather_alert_api_host()
        location = self._qweather_alert_location(resolved)
        if not host or location is None:
            return ""
        latitude, longitude = location
        # This is the current API Host route. The parser below also accepts
        # the legacy /v7/warning/now response for compatible gateways.
        path = "/weatheralert/v1/current/" + "/".join(
            (
                # The current endpoint documents a maximum of two decimal
                # places for path coordinates; keep the cache key precise,
                # but send the provider-compatible representation.
                self._qweather_alert_coordinate_text(latitude, decimals=2),
                self._qweather_alert_coordinate_text(longitude, decimals=2),
            )
        )
        return host + path + "?" + urlencode({"localTime": "true", "lang": "zh"})

    def _qweather_alert_headers(self) -> dict[str, str]:
        token = self._qweather_alert_token()
        if self._qweather_alert_credential_kind(token) == "api_key":
            return {
                "X-QW-Api-Key": token,
                "Accept": "application/json",
            }
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _normalize_weather_alert(raw: Any, *, source: str = "qweather") -> dict[str, Any]:
        """Normalize current and legacy QWeather alert objects."""

        if not isinstance(raw, dict):
            return {}
        message_type_raw = raw.get("messageType")
        message_type_code = ""
        supersedes: list[str] = []
        if isinstance(message_type_raw, dict):
            message_type_code = _qweather_alert_text(
                _qweather_alert_first(message_type_raw, "code", "name", "type"), 64
            )
            supersedes = _qweather_alert_string_list(message_type_raw.get("supersedes"), limit=32)
        else:
            message_type_code = _qweather_alert_text(message_type_raw, 64)
        if not supersedes:
            supersedes = _qweather_alert_string_list(raw.get("supersedes"), limit=32)
        event_type_raw = raw.get("eventType")
        event_name = ""
        event_code = ""
        if isinstance(event_type_raw, dict):
            event_name = _qweather_alert_text(_qweather_alert_first(event_type_raw, "name", "title"), 80)
            event_code = _qweather_alert_text(event_type_raw.get("code"), 64)
        else:
            event_name = _qweather_alert_text(event_type_raw, 80)
        if not event_name:
            event_name = _qweather_alert_text(
                _qweather_alert_first(raw, "typeName", "eventName", "type", "event"), 80
            )
        if not event_code:
            event_code = _qweather_alert_text(_qweather_alert_first(raw, "typeCode", "eventCode"), 64)
        color_name, color_code = _qweather_alert_color(
            raw.get("color", _qweather_alert_first(raw, "level", "warningLevel", "colorName"))
        )
        alert_id = _qweather_alert_text(
            _qweather_alert_first(raw, "id", "alertId", "warningId", "identifier"), 160
        )
        issued_time = _qweather_alert_text(
            _qweather_alert_first(raw, "issuedTime", "pubTime", "publishTime", "issuedAt"), 80
        )
        effective_time = _qweather_alert_text(
            _qweather_alert_first(raw, "effectiveTime", "effective", "startTime", "validFrom"), 80
        )
        onset_time = _qweather_alert_text(
            _qweather_alert_first(raw, "onsetTime", "onset", "beginTime"), 80
        )
        expire_time = _qweather_alert_text(
            _qweather_alert_first(raw, "expireTime", "expires", "expiresTime", "endTime", "ends"), 80
        )
        headline = _qweather_alert_text(
            _qweather_alert_first(raw, "headline", "title", "summary"), 240
        )
        description = _qweather_alert_text(
            _qweather_alert_first(raw, "description", "detail", "text"), 2000
        )
        sender = _qweather_alert_text(
            _qweather_alert_first(raw, "senderName", "sender", "publisher", "source"), 160
        )
        severity = _qweather_alert_text(raw.get("severity"), 32)
        status = _qweather_alert_text(raw.get("status"), 32)
        lower_message_type = message_type_code.lower()
        is_cancelled = any(token in lower_message_type for token in ("cancel", "撤销", "解除"))
        response_types = _qweather_alert_string_list(
            _qweather_alert_first(raw, "responseTypes", "response_types", "instructionTypes"),
            limit=16,
        )
        instruction = _qweather_alert_text(
            _qweather_alert_first(raw, "instruction", "instructions", "advice"), 1200
        )
        criteria = _qweather_alert_text(raw.get("criteria"), 600)
        fingerprint_source = "|".join(
            (
                alert_id,
                event_code or event_name,
                headline,
                sender,
                issued_time,
                message_type_code,
                color_code,
                severity,
                effective_time,
                onset_time,
                expire_time,
                description,
                instruction,
                ",".join(supersedes),
            )
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8", "ignore")).hexdigest()[:24]
        return {
            "id": alert_id,
            "source": _qweather_alert_text(source, 32) or "qweather",
            "sender": sender,
            "issued_time": issued_time,
            "message_type": message_type_code,
            "supersedes": supersedes,
            "event": event_name,
            "event_code": event_code,
            "urgency": _qweather_alert_text(raw.get("urgency"), 32),
            "severity": severity,
            "certainty": _qweather_alert_text(raw.get("certainty"), 32),
            "color": color_name,
            "color_code": color_code,
            "effective_time": effective_time,
            "onset_time": onset_time,
            "expire_time": expire_time,
            "headline": headline,
            "description": description,
            "criteria": criteria,
            "response_types": response_types,
            "instruction": instruction,
            "status": status,
            "is_cancelled": is_cancelled,
            "fingerprint": fingerprint,
        }

    @classmethod
    def _parse_qweather_alert_payload(cls, payload: Any) -> dict[str, Any]:
        """Parse a provider response, retaining all normalized alerts."""

        if not isinstance(payload, dict):
            return {"ok": False, "alerts": [], "error": "invalid_payload"}
        provider_code = str(payload.get("code") or "").strip()
        if provider_code and provider_code not in {"200", "0"}:
            return {
                "ok": False,
                "alerts": [],
                "error": "provider_code_" + _qweather_alert_text(provider_code, 32),
            }
        raw_alerts = payload.get("alerts")
        if raw_alerts is None:
            raw_alerts = payload.get("warning")
        if raw_alerts is None and isinstance(payload.get("data"), dict):
            raw_alerts = payload["data"].get("alerts", payload["data"].get("warning"))
        if not isinstance(raw_alerts, list):
            raw_alerts = []
        alerts = [cls._normalize_weather_alert(item) for item in raw_alerts]
        alerts = [item for item in alerts if item]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if not metadata and isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("metadata"), dict):
            metadata = payload["data"]["metadata"]
        update_time = _qweather_alert_text(
            _qweather_alert_first(payload, "updateTime", "updatedAt")
            or _qweather_alert_first(metadata, "updateTime", "updatedAt"),
            80,
        )
        tag = _qweather_alert_text(
            _qweather_alert_first(metadata, "tag") or payload.get("tag"),
            160,
        )
        attributions = _qweather_alert_string_list(
            metadata.get("attributions") or payload.get("attributions"),
            limit=4,
            item_limit=320,
        )
        zero_result = bool(
            metadata.get("zeroResult")
            or payload.get("zeroResult")
            or not alerts
        )
        return {
            "ok": True,
            "alerts": alerts,
            "zero_result": zero_result,
            "provider_update_time": update_time,
            "provider_tag": tag,
            "provider_attributions": attributions,
        }

    @classmethod
    def _normalize_qweather_alert_payload(cls, payload: Any) -> list[dict[str, Any]]:
        """Compatibility shorthand for callers that only need the list."""

        result = cls._parse_qweather_alert_payload(payload)
        return result.get("alerts", []) if result.get("ok") else []

    @classmethod
    def _dedupe_weather_alerts(cls, alerts: Any, *, limit: int = 64) -> list[dict[str, Any]]:
        if not isinstance(alerts, list):
            return []
        by_identity: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for raw in alerts:
            item = raw if isinstance(raw, dict) and "fingerprint" in raw else cls._normalize_weather_alert(raw)
            if not item:
                continue
            identity = str(item.get("id") or item.get("fingerprint") or "").strip()
            if not identity:
                continue
            if identity not in by_identity:
                order.append(identity)
                by_identity[identity] = item
                continue
            previous = by_identity[identity]
            # Prefer the newer revision, then the richer description. This
            # keeps an update from being hidden by a repeated old object.
            previous_issued = str(previous.get("issued_time") or "")
            current_issued = str(item.get("issued_time") or "")
            previous_richness = len(str(previous.get("description") or "")) + len(
                str(previous.get("instruction") or "")
            )
            current_richness = len(str(item.get("description") or "")) + len(
                str(item.get("instruction") or "")
            )
            if current_issued > previous_issued or (
                current_issued == previous_issued and current_richness > previous_richness
            ):
                by_identity[identity] = item
        return [by_identity[key] for key in order[: max(1, int(limit))]]

    @classmethod
    def _filter_weather_alerts(
        cls,
        alerts: Any,
        min_severity: Any = "blue",
    ) -> list[dict[str, Any]]:
        """Filter a normalized list without mutating the full cached list."""

        if not isinstance(alerts, list):
            return []
        threshold_text = _qweather_alert_text(min_severity, 32).strip().lower()
        if threshold_text in {"", "all", "any", "全部", "全部等级"}:
            return [item for item in alerts if isinstance(item, dict)]
        threshold = _qweather_alert_rank(threshold_text)
        result: list[dict[str, Any]] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            color = item.get("color_code") or item.get("color") or item.get("level")
            if color:
                # 颜色等级为准（蓝<黄<橙<红）。不能与 severity 取 max——qweather 的
                # severity 是国际档位（minor/moderate/severe/extreme），与国内颜色
                # 错位一档（黄色预警 severity=moderate），max 会让黄色顶穿 orange 阈值
                # （实测 08-14 黄色暴雨/雷电被误发）。颜色缺失（非中文/全球预警源）才
                # 退回 severity。
                rank = _qweather_alert_rank(color)
            else:
                rank = _qweather_alert_rank(item.get("severity"))
            if rank >= threshold:
                result.append(item)
        return result

    @classmethod
    def _weather_alert_identity(cls, alert: Any) -> str:
        if not isinstance(alert, dict):
            return ""
        return str(alert.get("id") or alert.get("fingerprint") or "").strip()

    @classmethod
    def _weather_alert_terminal_identity(cls, alert: Any) -> str:
        """Normalize resolved/cancelled variants to the warning they terminate."""
        if not isinstance(alert, dict):
            return ""
        supersedes = alert.get("supersedes")
        if isinstance(supersedes, list):
            for value in supersedes:
                identity = str(value or "").strip()
                if identity:
                    return identity
        return cls._weather_alert_identity(alert)

    @classmethod
    def _merge_weather_alert_cache(
        cls,
        cached: Any,
        alerts: Any,
        *,
        fetched_ts: float | None = None,
        config_key: str = "",
        provider_update_time: str = "",
        provider_tag: str = "",
        provider_attributions: Any = None,
        zero_result: bool = False,
    ) -> dict[str, Any]:
        """Build a bounded cache and expose changes for a future caller.

        ``new_alert_ids`` and ``resolved_alert_ids`` are metadata only; this
        helper does not send anything and the full ``alerts`` list is retained.
        """

        old_alerts = cached.get("alerts", []) if isinstance(cached, dict) else []
        old_items = cls._dedupe_weather_alerts(old_alerts)
        new_items = cls._dedupe_weather_alerts(alerts)
        old_by_id = {cls._weather_alert_identity(item): item for item in old_items}
        new_by_id = {cls._weather_alert_identity(item): item for item in new_items}
        old_ids = set(old_by_id)
        new_ids = set(new_by_id)
        updated_ids = {
            identity
            for identity in old_ids & new_ids
            if old_by_id[identity].get("fingerprint") != new_by_id[identity].get("fingerprint")
        }
        now = _safe_float(fetched_ts, _now_ts())
        if provider_attributions is None and isinstance(cached, dict):
            provider_attributions = cached.get("attributions")
        normalized_attributions = _qweather_alert_string_list(
            provider_attributions,
            limit=4,
            item_limit=320,
        )
        return {
            "version": 1,
            "source": "qweather",
            "config_key": _qweather_alert_text(config_key, 96),
            "alerts": new_items,
            "fetched_ts": now,
            "last_success_ts": now,
            "last_attempt_ts": now,
            "stale": False,
            "error": "",
            "zero_result": bool(zero_result or not new_items),
            "provider_update_time": _qweather_alert_text(provider_update_time, 80),
            "provider_tag": _qweather_alert_text(provider_tag, 160),
            "attributions": normalized_attributions,
            "new_alert_ids": sorted(identity for identity in new_ids - old_ids if identity),
            "updated_alert_ids": sorted(identity for identity in updated_ids if identity),
            "resolved_alert_ids": sorted(identity for identity in old_ids - new_ids if identity),
        }

    def _weather_alert_config_key(self) -> str:
        configured = self._qweather_configured_location()
        if configured:
            # Hash the configured identity rather than a resolved coordinate
            # so a city change invalidates alerts before the next GeoAPI call.
            location_text = self._qweather_location_identity()
        else:
            location = self._qweather_alert_location()
            if location is None:
                location_text = ""
            else:
                location_text = ",".join(self._qweather_alert_coordinate_text(value) for value in location)
        # Token changes do not alter the data location. Omitting it prevents a
        # credential-derived value from being persisted in the cache key.
        raw = "|".join(
            (
                self._qweather_alert_api_host(),
                location_text,
                self._weather_window_timezone(),
            )
        )
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24] if raw else ""

    def _weather_alert_cache_fresh(self, cache: Any, *, now: float | None = None) -> bool:
        if not isinstance(cache, dict) or cache.get("source") != "qweather":
            return False
        if cache.get("config_key") != self._weather_alert_config_key():
            return False
        current = _safe_float(now, _now_ts())
        # Failed requests use a short backoff based on last_attempt_ts; a
        # successful cache uses fetched_ts and remains available while stale.
        stamp = _safe_float(cache.get("last_attempt_ts"), 0)
        if not cache.get("stale"):
            stamp = _safe_float(cache.get("fetched_ts"), stamp)
        refresh_minutes = _safe_int(
            runtime_persona_setting(self, "weather_alert_refresh_minutes", 10),
            10,
            5,
            60,
        )
        return stamp > 0 and current - stamp < refresh_minutes * 60

    async def _fetch_qweather_alerts(self) -> dict[str, Any]:
        """Fetch and parse current alerts from QWeather's API Host route."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {"ok": False, "disabled": True, "alerts": [], "error": "disabled", "source": "qweather"}
        host = self._qweather_alert_api_host()
        token = self._qweather_alert_token()
        resolved = None
        if self._qweather_configured_location():
            resolved = await self._resolve_qweather_location()
        url = self._build_qweather_alert_url(resolved)
        if not host or not token or not url:
            return {
                "ok": False,
                "configured": False,
                "alerts": [],
                "error": "not_configured",
                "source": "qweather",
            }
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers=self._qweather_alert_headers(),
                    allow_redirects=False,
                ) as response:
                    if response.status == 204:
                        return {
                            "ok": True,
                            "alerts": [],
                            "zero_result": True,
                            "provider_update_time": "",
                            "provider_tag": "",
                            "source": "qweather",
                        }
                    if response.status != 200:
                        logger.debug("和风天气预警请求失败: %s", response.status)
                        return {
                            "ok": False,
                            "alerts": [],
                            "error": f"http_{response.status}",
                            "source": "qweather",
                        }
                    try:
                        payload = await response.json()
                    except TypeError:
                        # Some aiohttp-compatible test doubles and gateways do
                        # not expose a content type; allow the documented JSON.
                        payload = await response.json(content_type=None)
        except asyncio.TimeoutError:
            logger.warning("和风天气预警请求超时")
            return {"ok": False, "alerts": [], "error": "timeout", "source": "qweather"}
        except Exception as exc:
            logger.debug("和风天气预警获取失败: %s", _single_line(exc, 160))
            return {"ok": False, "alerts": [], "error": "request_failed", "source": "qweather"}
        parsed = self._parse_qweather_alert_payload(payload)
        parsed["source"] = "qweather"
        return parsed

    async def _fetch_weather_alerts(self) -> dict[str, Any]:
        return await self._fetch_qweather_alerts()

    async def _ensure_weather_alert_context(self, force: bool = False) -> dict[str, Any]:
        """Refresh the structured alert cache without dispatching messages."""

        if not bool(runtime_persona_setting(self, "enable_weather_alerts", False)):
            return {
                "version": 1,
                "source": "disabled",
                "alerts": [],
                "fetched_ts": 0,
                "stale": False,
                "error": "disabled",
            }
        data = getattr(self, "data", None)
        cached = data.get("weather_alerts", {}) if isinstance(data, dict) else {}
        if not force and self._weather_alert_cache_fresh(cached):
            result = deepcopy(cached)
            result["refreshed"] = False
            return result
        attempt_ts = _now_ts()
        current_config_key = self._weather_alert_config_key()
        fetched = await self._fetch_qweather_alerts()
        if fetched.get("ok"):
            result = self._merge_weather_alert_cache(
                cached,
                fetched.get("alerts", []),
                fetched_ts=attempt_ts,
                config_key=current_config_key,
                provider_update_time=fetched.get("provider_update_time", ""),
                provider_tag=fetched.get("provider_tag", ""),
                provider_attributions=fetched.get("provider_attributions"),
                zero_result=bool(fetched.get("zero_result")),
            )
            result["refreshed"] = True
        else:
            # Keep the last successful alerts during a provider outage. This
            # prevents a transient network error from erasing useful context,
            # but never carries alerts across a location/host change.
            same_config = isinstance(cached, dict) and cached.get("config_key") == current_config_key
            fetch_error = _qweather_alert_text(fetched.get("error"), 80)
            retain_after_failure = fetch_error not in {"not_configured", "disabled"}
            if same_config and cached.get("alerts") and retain_after_failure:
                result = deepcopy(cached)
                result["stale"] = True
                result["last_attempt_ts"] = attempt_ts
                result["error"] = fetch_error or "request_failed"
            else:
                result = {
                    "version": 1,
                    "source": "qweather",
                    "config_key": current_config_key,
                    "alerts": [],
                    "fetched_ts": 0,
                    "last_success_ts": 0,
                    "last_attempt_ts": attempt_ts,
                    "stale": bool(isinstance(cached, dict) and cached.get("alerts")),
                    "error": fetch_error or "request_failed",
                    "zero_result": False,
                    "attributions": [],
                    "new_alert_ids": [],
                    "updated_alert_ids": [],
                    "resolved_alert_ids": [],
                }
            result["refreshed"] = False
        if isinstance(data, dict):
            stored = deepcopy(result)
            stored.pop("refreshed", None)
            data["weather_alerts"] = stored
            saver = getattr(self, "_save_data_sync", None)
            if callable(saver):
                try:
                    saver(sections={"weather_alerts"})
                except Exception as exc:
                    logger.debug("保存天气预警缓存失败: %s", _single_line(exc, 160))
        return result
