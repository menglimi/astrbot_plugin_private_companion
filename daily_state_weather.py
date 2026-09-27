# -*- coding: utf-8 -*-
from .daily_state_weather_shared import (
    _QWEATHER_ALERT_COLOR_RANK,
    _QWEATHER_ALERT_SEVERITY_RANK,
    _now_ts,
    _openmeteo_weather_description,
    _qweather_alert_color,
    _qweather_alert_first,
    _qweather_alert_rank,
    _qweather_alert_string_list,
    _qweather_alert_text,
    _today_key,
    logger,
)
from .daily_state_weather_shared import logger
from .daily_state_weather_weather_context import DailyStateWeatherWeatherContextMixin
from .daily_state_weather_qweather_location import DailyStateWeatherQweatherLocationMixin
from .daily_state_weather_weather_alert import DailyStateWeatherWeatherAlertMixin
from .daily_state_weather_environment_change import DailyStateWeatherEnvironmentChangeMixin
from .daily_state_weather_alert_refresh_weather_fetch import DailyStateWeatherAlertRefreshWeatherFetchMixin
from .daily_state_weather_alert_event_queue import DailyStateWeatherAlertEventQueueMixin
from .daily_state_weather_shared import Any
class DailyStateWeatherMixin(DailyStateWeatherAlertEventQueueMixin, DailyStateWeatherAlertRefreshWeatherFetchMixin, DailyStateWeatherEnvironmentChangeMixin, DailyStateWeatherWeatherAlertMixin, DailyStateWeatherQweatherLocationMixin, DailyStateWeatherWeatherContextMixin):
    """天气域（从 DailyStateMixin 拆出）。"""


    # Alias kept intentionally small so integrations can use the generic name.
    # Keep a descriptive alias for integrations that use the "now" naming.
    def _build_qweather_now_url(self, resolved: Any = None) -> str:
        return self._build_qweather_weather_url(resolved)
