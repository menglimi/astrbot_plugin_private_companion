from __future__ import annotations
import asyncio
import base64
import copy
import importlib
import json
import os
import re
import secrets
import shutil
import sys
import time
import uuid
import zoneinfo
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context
from companion_v2.product import PRODUCT_VERSION
try:
    from astrbot.api.web import request
except Exception:
    request = None
from .helpers import _now_ts, _safe_float, _safe_int, _single_line
from .mobile_gateway import MOBILE_API_VERSION, MobileGatewayMixin
from .wakeup_alarm import WakeupAlarmMixin
try:
    from .mihome_data_manager import MiHomeDataManager
    from .mihome_client import MiHomeClient
    from .mihome_web_api import MiHomeWebAPI
except (ImportError, OSError, RuntimeError) as exc:
    MiHomeDataManager = None
    MiHomeClient = None
    MiHomeWebAPI = None
    _MIHOME_IMPORT_ERROR = str(exc)
else:
    _MIHOME_IMPORT_ERROR = ''
PLUGIN_NAME = 'astrbot_plugin_private_companion.reality'
PLUGIN_VERSION = PRODUCT_VERSION
PAGE_API_PREFIX = f'/{PLUGIN_NAME}/page'
MANAGED_PAGE_MESSAGE = '现实触及是 V2 内置能力，请在“陪伴小屋”继续操作。'
_MISSING = object()
_CAMERA_CONFIG_DEFAULTS: dict[str, Any] = {'enabled': False, 'index': 0, 'min_interval_seconds': 60, 'capture_timeout_seconds': 5, 'analysis_timeout_seconds': 25, 'proactive_curiosity_enabled': False, 'proactive_min_tier': 4, 'proactive_max_daily': 1, 'proactive_cooldown_minutes': 240}
_CAMERA_CONFIG_BOUNDS: dict[str, tuple[int, int]] = {'index': (0, 100000), 'min_interval_seconds': (10, 3600), 'capture_timeout_seconds': (2, 20), 'analysis_timeout_seconds': (5, 90), 'proactive_min_tier': (1, 5), 'proactive_max_daily': (0, 10), 'proactive_cooldown_minutes': (10, 1440)}
_LEGACY_CAMERA_CONFIG_KEYS: dict[str, str] = {'enabled': 'camera_enabled', 'index': 'camera_index', 'min_interval_seconds': 'camera_min_interval_seconds', 'capture_timeout_seconds': 'camera_capture_timeout_seconds', 'analysis_timeout_seconds': 'camera_analysis_timeout_seconds', 'proactive_curiosity_enabled': 'camera_proactive_curiosity_enabled', 'proactive_min_tier': 'camera_proactive_min_tier', 'proactive_max_daily': 'camera_proactive_max_daily', 'proactive_cooldown_minutes': 'camera_proactive_cooldown_minutes'}
_AUDIO_CONFIG_DEFAULTS: dict[str, Any] = {'default_playback_volume': 35}
_V2_PERCEPTION_FIELDS: dict[str, dict[str, Any]] = {'global': {'enabled': bool, 'vision_provider_id': 160, 'timezone': 80}, 'camera': {'camera_enabled': bool, 'camera_index': (0, 100000), 'min_interval_seconds': (10, 3600), 'capture_timeout_seconds': (2, 20), 'analysis_timeout_seconds': (5, 90), 'proactive_curiosity_enabled': bool, 'proactive_min_tier': (1, 5), 'proactive_max_daily': (0, 10), 'proactive_cooldown_minutes': (10, 1440)}, 'mobile': {'enabled': bool, 'host': 120, 'port': (1, 65535), 'public_base_url': 300, 'allowed_user_id': 120, 'session_ttl_hours': (1, 720), 'location_ttl_seconds': (60, 86400), 'amap_reverse_geocode_enabled': bool, 'amap_api_key': 240, 'amap_cache_ttl_seconds': (60, 604800), 'amap_request_timeout_seconds': (1, 20), 'telemetry_enabled': bool, 'telemetry_ttl_seconds': (60, 604800), 'activity_enabled': bool, 'activity_ttl_seconds': (60, 86400), 'screen_upload_enabled': bool, 'pairing_token': 240}, 'health': {'enabled': bool, 'ttl_seconds': (60, 604800)}}

def _reality_touch_tool_name(tool: Any) -> str:
    return _single_line(getattr(tool, 'name', ''), 80)

def _reality_touch_camera_tool_payload(tool_result: Any) -> dict[str, Any] | None:
    """Read the camera receipt from AstrBot's CallToolResult or test-friendly forms."""
    candidates: list[Any] = [tool_result]
    if not isinstance(tool_result, (str, bytes, dict)):
        candidates.extend(list(getattr(tool_result, 'content', []) or []))
        structured = getattr(tool_result, 'structuredContent', None)
        if structured is not None:
            candidates.append(structured)
    for candidate in candidates:
        if isinstance(candidate, dict):
            if 'status' in candidate or 'answer_available' in candidate:
                return candidate
            text = candidate.get('text')
        elif isinstance(candidate, bytes):
            text = candidate.decode('utf-8', errors='replace')
        elif isinstance(candidate, str):
            text = candidate
        else:
            text = getattr(candidate, 'text', None)
        if not isinstance(text, str):
            continue
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(decoded, dict) and ('status' in decoded or 'answer_available' in decoded):
            return decoded
    return None

def _reality_touch_camera_reply_needs_uncertain_fallback(text: str) -> bool:
    """Only catch explanations and guesses forbidden by an uncertain camera receipt."""
    return bool(re.search('(?:画面.{0,5}(?:黑|糊|模糊)|镜头.{0,5}(?:挡|遮)|看错了?|(?:又在)?吃(?:什么)?(?:不健康|东西)|老实交代|快点.{0,6}交代|你(?:刚才)?给我看了什么)', str(text or '')))

class RealityMiHomeExtensionAPI:
    """Embedded MiHome surface used by both Reality Companion pages."""

    def __init__(self, plugin: 'RealityRuntime') -> None:
        self._plugin = plugin

    def page_snapshot(self) -> dict[str, Any]:
        web_api = self._plugin.mihome_web_api
        return web_api.extension_snapshot() if web_api is not None else {'available': False}

    async def page_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str((payload or {}).get('action') or '').strip().lower()
        web_api = self._plugin.mihome_web_api
        if web_api is None:
            raise ValueError(self._plugin.mihome_unavailable_reason())
        handlers = {'start_login': lambda: web_api.extension_start_login(), 'login_status': lambda: web_api.extension_login_status((payload or {}).get('qr_revision', '')), 'sync_devices': web_api.extension_sync_devices, 'logout': web_api.extension_logout, 'save_mappings': lambda: web_api.extension_save_mappings((payload or {}).get('mappings', [])), 'device_status': lambda: web_api.extension_read_device_status(str((payload or {}).get('alias') or '')), 'control_power': lambda: web_api.extension_control_power(str((payload or {}).get('alias') or ''), bool((payload or {}).get('is_on')))}
        handler = handlers.get(action)
        if handler is None:
            raise ValueError('不支持的米家页面操作')
        result = await handler()
        return {'ok': True, 'result': result, 'data': web_api.extension_snapshot()}

class RealityExtensionAPI:
    """Internal Reality surface owned and composed by the V2 controller."""

    def __init__(self, plugin: 'RealityRuntime') -> None:
        self._plugin = plugin

    def companion_v2_status(self) -> dict[str, Any]:
        """Metadata only, without device queries, user records or session cleanup."""
        return {'schema': 'companion.provider-status.v1', 'plugin_id': PLUGIN_NAME, 'enabled': self._plugin._cfg_bool('enabled', True), 'runtime_ready': None}

    def companion_v2_native(self) -> Any | None:
        """Return the native Reality port owned by this V2 service instance."""
        return getattr(self._plugin, '_native_provider', None)

    def companion_v2_mobile_session_user(self, session_token: str) -> dict[str, str]:
        user_id = self._plugin._mobile_session_user_for_v2(session_token)
        return {'schema': 'companion.mobile-session.v1', 'user_id': user_id}

    def status(self) -> dict[str, Any]:
        return self._plugin.integration_status()

    def page_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._plugin._reality_touch_page_snapshot())

    def companion_v2_perception_settings(self) -> dict[str, Any]:
        plugin = self._plugin
        settings = plugin._reality_touch_configuration_snapshot()
        camera = plugin._reality_touch_camera_page_snapshot()
        camera['camera_enabled'] = camera.pop('global_enabled')
        vision_options = plugin._vision_provider_options()
        return {'schema': 'companion.perception-settings.v1', 'plugin_id': PLUGIN_NAME, 'global': {key: settings[key] for key in _V2_PERCEPTION_FIELDS['global']}, 'vision_providers': vision_options, 'camera': {key: camera[key] for key in _V2_PERCEPTION_FIELDS['camera']}, 'mobile': {key: settings['mobile'].get(key, '') for key in _V2_PERCEPTION_FIELDS['mobile'] if key not in {'pairing_token', 'amap_api_key'}}, 'health': {key: settings['health'][key] for key in _V2_PERCEPTION_FIELDS['health']}, 'secrets': {'pairing_token_configured': settings['mobile']['pairing_token_configured'], 'amap_api_key_configured': settings['mobile']['amap_api_key_configured']}}

    def companion_v2_set_health_source_enabled(self, enabled: bool) -> dict[str, Any]:
        """Toggle only the health ingestion source without rebuilding Reality.

        The perception page owns the single user-facing health switch.  A full
        ``configure_service`` would replace the phone gateway and discard its
        in-memory pairing sessions, so this narrow update changes the persisted
        health flag and refreshes the runtime fields in place.
        """
        if type(enabled) is not bool:
            raise ValueError('健康感知开关必须是布尔值')
        plugin = self._plugin
        plugin._set_group_config_field('health', 'enabled', enabled)
        saver = getattr(plugin.config, 'save_config', None)
        if callable(saver) and saver() is False:
            raise RuntimeError('健康感知设置保存失败')
        plugin._sync_runtime_config()
        return {'enabled': bool(plugin.health_enabled), 'ttl_seconds': int(plugin.health_ttl_seconds)}

    async def companion_v2_update_perception_settings(self, section: str, updates: dict[str, Any]) -> dict[str, Any]:
        rules = _V2_PERCEPTION_FIELDS.get(section)
        if rules is None or not isinstance(updates, dict) or (not updates) or set(updates) - set(rules):
            raise ValueError('现实感知配置字段无效')
        normalized = {}
        for key, value in updates.items():
            rule = rules[key]
            if rule is bool:
                if type(value) is not bool:
                    raise ValueError(f'{key} 必须是开关')
            elif isinstance(rule, tuple):
                if type(value) is not int or not rule[0] <= value <= rule[1]:
                    raise ValueError(f'{key} 超出允许范围')
            elif type(value) is not str or len(value) > rule:
                raise ValueError(f'{key} 内容无效')
            normalized[key] = value
        plugin = self._plugin
        registry = plugin.controller.native_extensions
        if registry is None or registry.service('reality') is not plugin:
            raise RuntimeError('现实感知模块已重新配置，请刷新页面')
        if section == 'camera':
            mapping = {'camera_enabled': 'enabled', 'camera_index': 'index'}
            updates = {'camera': {mapping.get(key, key): value for key, value in normalized.items()}}
        elif section == 'global':
            updates = normalized
        else:
            updates = {section: normalized}
        await registry.configure_service('reality', updates, plugin.config.revision())
        return registry.api('reality').companion_v2_perception_settings()

    async def page_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._plugin._perform_page_action(dict(payload or {}))

    def audio_consented(self, user_id: str) -> bool:
        user = self._plugin._user(user_id, create=False)
        return bool(user and self._plugin._reality_touch_audio_consented(user))

    def camera_user_eligible(self, user_id: str) -> bool:
        return self._plugin._reality_touch_camera_user_eligible(user_id)

    def camera_proactive_state(self, user_id: str) -> dict[str, Any]:
        user = self._plugin._user(user_id, create=False)
        if not user:
            return {'available': False, 'direct_allowed': False, 'reason': 'user_missing'}
        return self._plugin._reality_touch_camera_proactive_state(user, user_id=user_id)

    def camera_proactive_prompt(self, user_id: str) -> str:
        user = self._plugin._user(user_id, create=False)
        return self._plugin._reality_touch_camera_proactive_prompt(user, user_id=user_id) if user else ''

    def proactive_voice_allowed(self, user_id: str) -> bool:
        user = self._plugin._user(user_id, create=False)
        return bool(user and self._plugin._reality_touch_proactive_voice_allowed(user))

    async def mirror_proactive_voice(self, user_id: str, audio_path: str) -> bool:
        user = self._plugin._user(user_id, create=False)
        return bool(user and await self._plugin._mirror_reality_touch_proactive_voice(user, audio_path))

    async def schedule_reminder(self, user_id: str, payload: dict[str, Any], *, source_text: str, trigger_umo: str='') -> bool:
        user = self._plugin._user(user_id)
        if trigger_umo:
            user['umo'] = _single_line(trigger_umo, 180)
        self._plugin._save_data_sync()
        return await self._plugin._schedule_reality_touch_official_reminder(user_id, payload, source_text=source_text, trigger_umo=trigger_umo)

    async def camera_snapshot(self, user_id: str, purpose: str, *, source: str='manual') -> dict[str, Any]:
        return await self._plugin._reality_touch_camera_snapshot_for_user(user_id, purpose, source=source)

    def audio_command(self, user_id: str, value: str, *, umo: str='') -> tuple[str, Any]:
        user = self._plugin._user(user_id)
        if umo:
            user['umo'] = _single_line(umo, 180)
        result = self._plugin._wakeup_alarm_command(user, value)
        self._plugin._save_data_sync()
        return result

    async def test_wakeup(self, user_id: str) -> None:
        user = self._plugin._user(user_id)
        await self._plugin._test_wakeup_alarm(user)

    def mobile_context(self, user_id: str='') -> dict[str, Any]:
        return self._plugin.mobile_context(user_id)

    def wakeup_snapshot(self, user_id: str='') -> dict[str, Any]:
        """Return only the wake session state; never message bodies or feedback text."""
        user = self._plugin._user(user_id, create=False)
        if not isinstance(user, dict):
            return {}
        alarm = user.get('wakeup_alarm')
        alarm = alarm if isinstance(alarm, dict) else {}
        session = alarm.get('contact_session')
        session = session if isinstance(session, dict) else {}
        safe = {}
        for key in ('id', 'status', 'attempt', 'max_attempts', 'triggered_at',
                    'feedback_at', 'completed_at', 'next_attempt_at'):
            value = session.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                safe[key] = value
        return {'enabled': bool(alarm.get('enabled')),
                'time': _single_line(alarm.get('time'), 8),
                'contact_session': safe}

    def snapshot_owner(self, host_api: Any) -> Any:
        """Bind the native RV-1 owner to the current trusted host authority."""
        owner = self._plugin._mobile_snapshot_owner
        owner.provider_current = lambda: getattr(self._plugin.controller, 'native_extensions', None) is not None and self._plugin.controller.native_extensions.api('reality') is self
        return owner.bind(host_api)

    async def report_telemetry(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Accept structured data from another trusted AstrBot plugin."""
        normalized_user_id = _single_line(user_id, 120)
        if not normalized_user_id:
            return {'ok': False, 'reason': 'user_id_missing'}
        if not self._plugin._mobile_telemetry_enabled():
            return {'ok': False, 'reason': 'telemetry_disabled'}
        normalized = self._plugin._normalize_mobile_telemetry_payload(dict(payload or {}))
        if normalized is None:
            return {'ok': False, 'reason': 'invalid_or_expired_payload'}
        data = self._plugin._store_mobile_telemetry(normalized_user_id, normalized)
        return {'ok': True, 'data': data}

    async def record_reality_touch_output(self, user_id: str, text: str, *, source: str='reality_touch_audio', delivered_at: float | None=None) -> dict[str, Any]:
        """Persist actual device output in V2 Reality's own store."""
        normalized_user_id = _single_line(user_id, 120)
        visible = _single_line(text, 500)
        if not normalized_user_id or not visible:
            return {'recorded': False, 'reason': 'invalid_payload'}
        timestamp = _safe_float(delivered_at, _now_ts(), 0.0) or _now_ts()
        host_identity = self._plugin._host_identity(normalized_user_id)
        subject_ref = _single_line(host_identity.get('reality_subject_ref'), 160) or normalized_user_id
        outputs = self._plugin.data.setdefault('reality_touch_outputs', {})
        if not isinstance(outputs, dict):
            outputs = {}
            self._plugin.data['reality_touch_outputs'] = outputs
        output = {'text': visible, 'source': _single_line(source, 80) or 'reality_touch_audio', 'delivered_at': timestamp}
        outputs[subject_ref] = output
        user = self._plugin._user(normalized_user_id, create=True)
        user['last_proactive_message'] = visible
        user['last_proactive_sent_at'] = timestamp
        self._plugin._save_data_sync()
        return {'recorded': True, 'user_id': normalized_user_id, 'delivered_at': timestamp}

    def recent_output(self, user_id: str) -> dict[str, Any]:
        """Return a detached continuity record owned by this plugin."""
        normalized = _single_line(user_id, 120)
        host_identity = self._plugin._host_identity(normalized)
        subject_ref = _single_line(host_identity.get('reality_subject_ref'), 160) or normalized
        outputs = self._plugin.data.get('reality_touch_outputs')
        output = outputs.get(subject_ref) if isinstance(outputs, dict) else None
        if not isinstance(output, dict):
            user = self._plugin._user(normalized, create=False)
            output = user.get('last_reality_touch_output') if isinstance(user, dict) else None
        return copy.deepcopy(output) if isinstance(output, dict) else {}

    def apply_pending_confirmation(self, user_id: str, text: str) -> str | None:
        user = self._plugin._user(user_id, create=False)
        return self._plugin._reality_touch_apply_pending_confirmation(user, text) if user else None

__all__ = [name for name in globals() if not name.startswith("__")]
