"""Small, consent-first HTTP bridge for the Android companion app.

The gateway intentionally lives beside Reality Companion.  It exposes only
device-facing operations and keeps location/session state in memory so a
restart drops the most sensitive context automatically.
"""
from __future__ import annotations
import asyncio
import contextvars
import functools
import hashlib
import hmac
import inspect
import ipaddress
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from .amap_geocode import amap_cache_key, reverse_geocode
try:
    from quart import request
except Exception:
    request = None
from astrbot.api import logger
from companion_v2.product import PRODUCT_VERSION
try:
    import aiohttp
    from aiohttp import web
except Exception:
    aiohttp = None
    web = None
PLUGIN_NAME = 'astrbot_plugin_private_companion.reality'
MOBILE_API_VERSION = '1.0'
MOBILE_MAX_BODY_BYTES = 256 * 1024
MOBILE_STATUS_DISCOVERY_TIMEOUT_SECONDS = 4.0
MOBILE_MAX_SESSIONS_PER_USER = 8
MOBILE_PROXY_MAX_WS_BYTES = 16 * 1024 * 1024
MOBILE_TELEMETRY_MAX_MEASUREMENTS = 32
MOBILE_TELEMETRY_MAX_TYPES = 24
MOBILE_TELEMETRY_MAX_ACTIVITY_METRICS = 8
MOBILE_TELEMETRY_MAX_EVENTS = 24
MOBILE_TELEMETRY_MAX_EVENT_ITEMS = 16
MOBILE_TELEMETRY_MAX_EVENT_METRICS = 8
MOBILE_TELEMETRY_MAX_LABEL = 80
MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE = 40
MOBILE_ACTIVITY_MAX_LABEL = 80
MOBILE_ACTIVITY_CATEGORIES = {'video', 'music', 'browser', 'game', 'social', 'work', 'reading', 'private', 'other'}
MOBILE_PRIVATE_APP_MARKERS = ('bank', 'finance', 'wallet', 'password', 'authenticator', 'otp', 'mail', 'email', 'message', 'sms', 'chat', 'wechat', 'qq', 'telegram', 'signal', 'whatsapp', 'gov', 'health')
MOBILE_ACTIVITY_LABEL_BLOCKLIST = ('忽略', '系统提示', '提示词', '指令', 'prompt', 'instruction', '执行命令')

@dataclass(slots=True)
class _MobileRequestState:
    headers: dict[str, str] = field(default_factory=dict)
    host: str = ''
    remote_addr: str = ''
    payload: dict[str, Any] = field(default_factory=dict)
_mobile_request_state: contextvars.ContextVar[_MobileRequestState | None] = contextvars.ContextVar('reality_companion_mobile_request', default=None)

def _clean(value: Any, limit: int=160) -> str:
    return ' '.join(str(value or '').replace('\x00', ' ').split())[:limit]

def _number(value: Any, default: float=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _telemetry_identifier(value: Any, limit: int=40) -> str:
    """Normalize a machine-readable telemetry key without accepting prompt text."""
    value = _clean(value, limit).lower()
    if not value or not value.isascii() or (not all((char.isalnum() or char in '_.-' for char in value))):
        return ''
    return value

def _telemetry_label(value: Any, limit: int=MOBILE_TELEMETRY_MAX_LABEL) -> str:
    label = _clean(value, limit)
    if not label or any((marker in label.casefold() for marker in MOBILE_ACTIVITY_LABEL_BLOCKLIST)):
        return ''
    return label

class MobileGatewayMixin:
    """Expose the minimum bridge needed by the mobile companion client."""

    def _mobile_gateway_init(self) -> None:
        self._mobile_sessions: dict[str, dict[str, Any]] = {}
        self._mobile_locations: dict[str, dict[str, Any]] = {}
        self._mobile_amap_cache: dict[str, dict[str, Any]] = {}
        self._mobile_device_status: dict[str, dict[str, Any]] = {}
        self._mobile_activity: dict[str, dict[str, Any]] = {}
        self._mobile_telemetry: dict[str, dict[str, Any]] = {}
        self._mobile_screen_clients: dict[str, dict[str, Any]] = {}
        self._mobile_pair_attempts: dict[str, list[float]] = {}
        self._mobile_server_runner: Any | None = None
        self._mobile_server_site: Any | None = None
        self._mobile_server_bound_port = 0
        self._mobile_room_start_lock = asyncio.Lock()
        self._mobile_state_lock = threading.RLock()
        self._mobile_runtime_stopped = False
        self._mobile_location_notify_tasks: dict[str, asyncio.Task] = {}
        self._mobile_location_notify_pending: set[str] = set()
        self._mobile_location_enrich_tasks: dict[str, asyncio.Task] = {}
        self._mobile_proxy_session: Any | None = None
        self._mobile_room_upstream_cache: dict[str, str] = {}
        from .mobile_snapshot_owner import MobileSnapshotOwner
        self._mobile_snapshot_owner = MobileSnapshotOwner(self)

    def _mobile_enabled(self) -> bool:
        return bool(self._cfg_bool('mobile.enabled', False))

    def _mobile_pairing_token(self) -> str:
        return self._cfg_str('mobile.pairing_token', '')

    def _mobile_host(self) -> str:
        return self._cfg_str('mobile.host', '0.0.0.0') or '0.0.0.0'

    def _mobile_port(self) -> int:
        return self._cfg_int('mobile.port', 6322, 1, 65535)

    def _mobile_session_ttl(self) -> int:
        hours = self._cfg_int('mobile.session_ttl_hours', 168, 1, 720)
        return hours * 60 * 60

    def _mobile_core_user_id(self) -> str:
        getter = getattr(self, 'v2_host_api', None)
        api = getter() if callable(getter) else None
        core_ids_getter = getattr(api, 'mobile_core_pairing_candidates', None) if api is not None else None
        if not callable(core_ids_getter):
            return ''
        try:
            values = core_ids_getter()
        except Exception:
            values = []
        candidates = {_clean(item, 120) for item in values if _clean(item, 120)} \
            if isinstance(values, (list, tuple, set)) else set()
        return next(iter(candidates)) if len(candidates) == 1 else ''

    def _mobile_allowed_user_id(self) -> str:
        configured = self._cfg_str('mobile.allowed_user_id', '')
        if configured:
            return configured
        getter = getattr(self, 'v2_host_api', None)
        api = getter() if callable(getter) else None
        core_ids_getter = getattr(api, 'mobile_core_pairing_candidates', None) if api is not None else None
        if callable(core_ids_getter):
            return self._mobile_core_user_id()
        ids_getter = getattr(api, 'mobile_pairing_candidates', None)
        try:
            values = ids_getter() if callable(ids_getter) else []
        except Exception:
            values = []
        candidates: set[str] = set()
        if isinstance(values, (list, tuple, set)):
            candidates = {_clean(item, 120) for item in values if _clean(item, 120)}
        return next(iter(candidates)) if len(candidates) == 1 else ''

    def _mobile_location_ttl(self) -> int:
        return max(60, min(24 * 60 * 60, self._cfg_int('mobile.location_ttl_seconds', 900, 60, 24 * 60 * 60)))

    def _mobile_amap_api_key(self) -> str:
        return self._cfg_str('mobile.amap_api_key', '')

    def _mobile_amap_enabled(self) -> bool:
        return bool(self._mobile_amap_api_key()) and self._cfg_bool('mobile.amap_reverse_geocode_enabled', False)

    def _mobile_amap_cache_ttl(self) -> int:
        return max(60, min(7 * 24 * 60 * 60, self._cfg_int('mobile.amap_cache_ttl_seconds', 1800, 60, 7 * 24 * 60 * 60)))

    def _mobile_amap_timeout_seconds(self) -> float:
        return max(1.0, min(20.0, _number(self._cfg_str('mobile.amap_request_timeout_seconds', '5'), 5.0)))

    async def _mobile_enrich_location_with_amap(self, item: dict[str, Any]) -> None:
        if not self._mobile_amap_enabled() or not isinstance(item, dict):
            return
        key = amap_cache_key(item.get('latitude'), item.get('longitude'))
        if not key:
            return
        now = time.time()
        cached = self._mobile_amap_cache.get(key)
        if isinstance(cached, dict) and now - _number(cached.get('cached_at')) <= self._mobile_amap_cache_ttl():
            item['amap'] = dict(cached.get('result') or {})
            return
        result = await reverse_geocode(item.get('latitude'), item.get('longitude'), api_key=self._mobile_amap_api_key(), timeout_seconds=self._mobile_amap_timeout_seconds())
        if not isinstance(result, dict):
            return
        self._mobile_amap_cache[key] = {'cached_at': now, 'result': dict(result)}
        if len(self._mobile_amap_cache) > 128:
            oldest = sorted(self._mobile_amap_cache.items(), key=lambda pair: _number(pair[1].get('cached_at')))[:32]
            for old_key, _value in oldest:
                self._mobile_amap_cache.pop(old_key, None)
        item['amap'] = result

    def _schedule_mobile_amap_enrichment(self, user_id: str, item: dict[str, Any]) -> None:
        """Enrich the newest location without delaying the mobile upload response."""
        if not self._mobile_amap_enabled() or not isinstance(item, dict):
            return
        previous = self._mobile_location_enrich_tasks.get(user_id)
        if previous is not None and (not previous.done()):
            previous.cancel()

        async def run() -> None:
            try:
                await self._mobile_enrich_location_with_amap(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug('[RealityCompanion] 高德区域增强失败: %s', _clean(exc, 160))
        task = asyncio.create_task(run(), name='reality_mobile_amap_enrichment')
        self._mobile_location_enrich_tasks[user_id] = task

        def done(_completed: asyncio.Task) -> None:
            if self._mobile_location_enrich_tasks.get(user_id) is task:
                self._mobile_location_enrich_tasks.pop(user_id, None)
        task.add_done_callback(done)

    def _mobile_screen_upload_enabled(self) -> bool:
        return bool(self._cfg_bool('mobile.screen_upload_enabled', True))

    def _mobile_telemetry_enabled(self) -> bool:
        if hasattr(self, 'health_enabled'):
            return bool(getattr(self, 'health_enabled'))
        return bool(self._cfg_bool('mobile.telemetry_enabled', False))

    def _mobile_telemetry_ttl(self) -> int:
        configured = getattr(self, 'health_ttl_seconds', None)
        if configured is not None:
            return max(60, min(7 * 24 * 60 * 60, int(configured)))
        return max(60, min(7 * 24 * 60 * 60, self._cfg_int('mobile.telemetry_ttl_seconds', 3600, 60, 7 * 24 * 60 * 60)))

    def _mobile_activity_enabled(self) -> bool:
        return bool(self._cfg_bool('mobile.activity_enabled', False))

    def _mobile_activity_ttl(self) -> int:
        return max(60, min(24 * 60 * 60, self._cfg_int('mobile.activity_ttl_seconds', 900, 60, 24 * 60 * 60)))

    def _mobile_token_from_request(self) -> str:
        current = _mobile_request_state.get()
        request_obj = current if current is not None else request
        if request_obj is None:
            return ''
        header_name = 'x-companion-mobile-token' if current is not None else 'X-Companion-Mobile-Token'
        auth_name = 'authorization' if current is not None else 'Authorization'
        token = _clean(request_obj.headers.get(header_name), 240)
        if token:
            return token
        auth = _clean(request_obj.headers.get(auth_name), 260)
        if auth.lower().startswith('bearer '):
            return auth[7:].strip()[:240]
        return ''

    async def _mobile_request_payload(self) -> dict[str, Any]:
        current = _mobile_request_state.get()
        if current is not None:
            return dict(current.payload)
        if request is None:
            return {}
        payload = await request.get_json(silent=True) or {}
        return payload if isinstance(payload, dict) else {}

    def _mobile_request_host(self) -> str:
        current = _mobile_request_state.get()
        if current is not None:
            return _clean(current.host, 180)
        if request is None:
            return ''
        try:
            return _clean(getattr(request, 'host', ''), 180)
        except RuntimeError:
            return ''

    def _mobile_request_header(self, name: str) -> str:
        current = _mobile_request_state.get()
        if current is not None:
            return _clean(current.headers.get(name.lower(), ''), 240)
        if request is None:
            return ''
        try:
            return _clean(request.headers.get(name, ''), 240)
        except RuntimeError:
            return ''

    def _mobile_external_origin(self) -> str:
        """Resolve the public origin used in mobile room links."""
        configured = self._cfg_str('mobile.public_base_url', '').rstrip('/')
        candidates: list[tuple[str, str]] = []
        if configured:
            candidates.append((configured, 'configured'))
        forwarded_host = self._mobile_request_header('X-Forwarded-Host')
        request_host = self._mobile_request_header('Host')
        socket_host = self._mobile_request_host()
        host_value = forwarded_host or request_host or socket_host
        host_value = host_value.split(',', 1)[0].strip()
        forwarded_proto = self._mobile_request_header('X-Forwarded-Proto')
        scheme_value = (forwarded_proto or 'http').split(',', 1)[0].strip().lower()
        if host_value:
            source = 'socket' if not forwarded_host and (not request_host) else 'request'
            candidates.append((f'{scheme_value}://{host_value}', source))
        for raw, source in candidates:
            try:
                parsed = urlsplit(raw if '://' in raw else f'//{raw}')
                scheme = str(parsed.scheme or scheme_value or 'http').lower()
                if scheme not in {'http', 'https'} or not parsed.netloc:
                    continue
                host = self._mobile_normalize_host(parsed.netloc)
                if not host:
                    continue
                netloc = f'[{host}]' if ':' in host else host
                if parsed.port is not None:
                    netloc = f'{netloc}:{parsed.port}'
                elif source == 'socket':
                    port = self._mobile_server_bound_port or self._mobile_port()
                    if port:
                        netloc = f'{netloc}:{port}'
                path = str(parsed.path or '').rstrip('/')
                return urlunsplit((scheme, netloc, path, '', ''))
            except (TypeError, ValueError):
                logger.debug('[RealityCompanion] 忽略无效移动端外部地址: source=%s value=%s', source, _clean(raw, 180))
        return ''

    def _mobile_request_remote_addr(self) -> str:
        current = _mobile_request_state.get()
        if current is not None:
            return _clean(current.remote_addr, 96)
        return _clean(getattr(request, 'remote_addr', ''), 96) if request is not None else ''

    @staticmethod
    def _mobile_normalize_host(value: Any) -> str:
        raw = _clean(value, 180).split(',', 1)[0].strip()
        if not raw or any((char in raw for char in ('/', '\\', '@', '?', '#'))):
            return ''
        try:
            parsed = urlsplit(f'//{raw}')
            if parsed.username or parsed.password:
                return ''
            host = str(parsed.hostname or '').strip().rstrip('.')
        except (TypeError, ValueError):
            return ''
        if not host:
            return ''
        try:
            address = ipaddress.ip_address(host)
            return '' if address.is_unspecified else str(address)
        except ValueError:
            try:
                ascii_host = host.encode('idna').decode('ascii').lower()
            except UnicodeError:
                return ''
            labels = ascii_host.split('.')
            if any((not label or len(label) > 63 or (not label[0].isalnum()) or (not label[-1].isalnum()) or any((not (char.isalnum() or char == '-') for char in label)) for label in labels)):
                return ''
            return ascii_host

    @staticmethod
    def _mobile_token_key(token: str) -> str:
        return hashlib.sha256(token.encode('utf-8', errors='ignore')).hexdigest()

    def _mobile_cleanup_sessions(self) -> None:
        now = time.time()
        with self._mobile_state_lock:
            expired = [key for key, item in self._mobile_sessions.items() if not isinstance(item, dict) or _number(item.get('expires_at')) <= now]
            for key in expired:
                removed = self._mobile_sessions.pop(key, None)
                if isinstance(removed, dict) and removed.get('user_id'):
                    self._mobile_snapshot_owner.session_changed(removed['user_id'])
            stale_locations = [user_id for user_id, item in self._mobile_locations.items() if not isinstance(item, dict) or now - _number(item.get('received_at') or item.get('captured_at')) > self._mobile_location_ttl()]
            for user_id in stale_locations:
                self._mobile_locations.pop(user_id, None)
                self._mobile_snapshot_owner.invalidate(user_id, ('location',))
            stale_devices = [user_id for user_id, item in self._mobile_device_status.items() if not isinstance(item, dict) or now - _number(item.get('received_at')) > 24 * 60 * 60]
            for user_id in stale_devices:
                self._mobile_device_status.pop(user_id, None)
                self._mobile_snapshot_owner.invalidate(user_id, ('device',))
            stale_telemetry = [user_id for user_id, item in self._mobile_telemetry.items() if not isinstance(item, dict) or now - _number(item.get('received_at')) > self._mobile_telemetry_ttl()]
            for user_id in stale_telemetry:
                self._mobile_telemetry.pop(user_id, None)
                self._mobile_snapshot_owner.invalidate(user_id, ('telemetry',))
            stale_activity = [user_id for user_id, item in self._mobile_activity.items() if not isinstance(item, dict) or now - _number(item.get('received_at')) > self._mobile_activity_ttl()]
            for user_id in stale_activity:
                self._mobile_activity.pop(user_id, None)
                self._mobile_snapshot_owner.invalidate(user_id, ('activity',))
            stale_screen_clients = [user_id for user_id, item in self._mobile_screen_clients.items() if not isinstance(item, dict) or now - _number(item.get('last_seen_at')) > 5 * 60]
            for user_id in stale_screen_clients:
                self._mobile_screen_clients.pop(user_id, None)

    def _mobile_authorize(self, *, allow_pairing: bool=False) -> dict[str, Any] | None:
        if self._mobile_runtime_stopped or not self._mobile_enabled():
            return None
        token = self._mobile_token_from_request()
        if not token:
            return None
        if allow_pairing:
            configured = self._mobile_pairing_token()
            if configured and hmac.compare_digest(token, configured):
                return {'user_id': self._mobile_allowed_user_id(), 'pairing': True}
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            session = self._mobile_sessions.get(self._mobile_token_key(token))
            if not isinstance(session, dict):
                return None
            if not self._mobile_snapshot_owner.session_authorized(session):
                self._mobile_sessions.pop(self._mobile_token_key(token), None)
                self._mobile_snapshot_owner.session_changed(_clean(session.get('user_id'), 120))
                return None
            session['last_seen_at'] = time.time()
            return dict(session)

    def _mobile_session_user_for_v2(self, token: str) -> str:
        """Attest an active paired session to the local V2 host without returning its token."""
        if type(token) is not str or not token or self._mobile_runtime_stopped or (not self._mobile_enabled()):
            return ''
        self._mobile_cleanup_sessions()
        key = self._mobile_token_key(token)
        with self._mobile_state_lock:
            session = self._mobile_sessions.get(key)
            if not isinstance(session, dict) or not self._mobile_snapshot_owner.session_authorized(session):
                if isinstance(session, dict):
                    self._mobile_sessions.pop(key, None)
                    self._mobile_snapshot_owner.session_changed(_clean(session.get('user_id'), 120))
                return ''
            session['last_seen_at'] = time.time()
            return _clean(session.get('user_id'), 120)

    def _mobile_pair_rate_limited(self, *, succeeded: bool=False) -> bool:
        key = self._mobile_request_remote_addr() or 'unknown'
        now = time.time()
        with self._mobile_state_lock:
            self._mobile_pair_attempts = {remote: [value for value in values if now - value <= 60] for remote, values in self._mobile_pair_attempts.items() if any((now - value <= 60 for value in values))}
            attempts = [value for value in self._mobile_pair_attempts.get(key, []) if now - value <= 60]
            if succeeded:
                self._mobile_pair_attempts.pop(key, None)
                return False
            limited = len(attempts) >= 8
            if not limited:
                attempts.append(now)
                self._mobile_pair_attempts[key] = attempts
            return limited

    def _mobile_store_session(self, token: str, session: dict[str, Any]) -> None:
        user_id = _clean(session.get('user_id'), 120)
        with self._mobile_state_lock:
            existing = [(key, item) for key, item in self._mobile_sessions.items() if isinstance(item, dict) and _clean(item.get('user_id'), 120) == user_id]
            if len(existing) >= MOBILE_MAX_SESSIONS_PER_USER:
                oldest_key, _ = min(existing, key=lambda pair: _number(pair[1].get('created_at')))
                self._mobile_sessions.pop(oldest_key, None)
            self._mobile_sessions[self._mobile_token_key(token)] = session
            self._mobile_snapshot_owner.session_changed(user_id, session)

    @staticmethod
    def _mobile_json_error(message: str, status: int=400) -> tuple[dict[str, Any], int]:
        return ({'ok': False, 'message': _clean(message, 240)}, status)

    def _mobile_reality_status(self) -> dict[str, Any]:
        status_getter = getattr(self, 'integration_status', None)
        try:
            value = status_getter() if callable(status_getter) else {}
        except Exception:
            value = {}
        return value if isinstance(value, dict) else {}

    def _mobile_location_snapshot(self, user_id: str, *, prompt_safe: bool=False) -> dict[str, Any]:
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            stored = self._mobile_locations.get(_clean(user_id, 120))
            item = dict(stored) if isinstance(stored, dict) else None
        if not isinstance(item, dict):
            return {'available': False, 'reason': 'no_recent_location'}
        captured_at = _number(item.get('captured_at'))
        received_at = _number(item.get('received_at') or captured_at)
        age = max(0, int(time.time() - captured_at)) if captured_at else 0
        retention_age = max(0, int(time.time() - received_at)) if received_at else 0
        if not captured_at or not received_at or retention_age > self._mobile_location_ttl():
            return {'available': False, 'reason': 'location_expired', 'age_seconds': age}
        place = item.get('place') if isinstance(item.get('place'), dict) else {}
        prompt_place = {'matched': bool(place.get('matched')), 'name': _clean(place.get('name'), 40), 'kind': _clean(place.get('kind'), 24), 'distance_m': round(max(0.0, _number(place.get('distance_m'))), 0), 'radius_m': round(max(0.0, _number(place.get('radius_m'))), 0)}
        confidence = _clean(place.get('confidence'), 32)
        aliases = [_clean(item, 40) for item in list(place.get('aliases') or [])[:8] if _clean(item, 40)]
        parent_name = _clean(place.get('parent_name'), 40)
        if confidence:
            prompt_place['confidence'] = confidence
        if aliases:
            prompt_place['aliases'] = aliases
        if parent_name:
            prompt_place['parent_name'] = parent_name
        amap = item.get('amap') if isinstance(item.get('amap'), dict) else {}
        if prompt_safe:
            area_label = _clean(amap.get('area_label'), 100)
            if area_label:
                prompt_place['area_label'] = area_label
                prompt_place['area_source'] = 'amap'
        elif amap:
            prompt_place['amap'] = {key: _clean(amap.get(key), 180) for key in ('province', 'city', 'district', 'township', 'street', 'number', 'poi', 'formatted_address', 'area_label', 'source') if _clean(amap.get(key), 180)}
        return {'available': True, 'latitude': round(_number(item.get('latitude')), 3), 'longitude': round(_number(item.get('longitude')), 3), 'accuracy_m': round(max(0.0, _number(item.get('accuracy_m'))), 1), 'altitude_m': round(_number(item.get('altitude_m')), 1) if item.get('altitude_m') is not None else None, 'speed_mps': round(max(0.0, _number(item.get('speed_mps'))), 1), 'bearing': round(_number(item.get('bearing')), 1) if item.get('bearing') is not None else None, 'label': '' if prompt_safe else _clean(item.get('label'), 40), 'place': prompt_place, 'captured_at': captured_at, 'age_seconds': age, 'source': 'android_foreground_location'}

    def mobile_context(self, user_id: str='') -> dict[str, Any]:
        """Return authorized, coarse mobile facts for V2's current private window."""
        normalized = _clean(user_id, 120)
        if self._mobile_runtime_stopped or not self._mobile_enabled():
            snapshot = {'available': False, 'reason': 'mobile_gateway_disabled'}
        else:
            snapshot = self._mobile_location_snapshot(normalized, prompt_safe=True) if normalized else {'available': False, 'reason': 'user_missing'}
        device_snapshot = self._mobile_device_snapshot(normalized) if normalized and (not self._mobile_runtime_stopped) and self._mobile_enabled() else {'available': False, 'reason': 'mobile_gateway_disabled'}
        return {'available': bool(snapshot.get('available')), 'user_id': normalized, 'location': snapshot, 'device': device_snapshot, 'activity': self._mobile_activity_snapshot(normalized, prompt_safe=True) if normalized and (not self._mobile_runtime_stopped) and self._mobile_enabled() else {'available': False, 'reason': 'mobile_gateway_disabled'}, 'telemetry': self._mobile_telemetry_snapshot(normalized, prompt_safe=True) if normalized and (not self._mobile_runtime_stopped) and self._mobile_enabled() else {'available': False, 'reason': 'mobile_gateway_disabled'}, 'privacy': {'coordinates_rounded': True, 'foreground_only': True, 'device_status_short_lived': True, 'telemetry_short_lived': True, 'telemetry_prompt_safe_projection': True, 'expires_after_seconds': self._mobile_location_ttl(), 'telemetry_expires_after_seconds': self._mobile_telemetry_ttl(), 'activity_expires_after_seconds': self._mobile_activity_ttl()}}

    def _mobile_activity_snapshot(self, user_id: str, *, prompt_safe: bool=False) -> dict[str, Any]:
        if not self._mobile_activity_enabled():
            return {'available': False, 'reason': 'activity_disabled'}
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            stored = self._mobile_activity.get(_clean(user_id, 120))
            item = dict(stored) if isinstance(stored, dict) else None
        if not isinstance(item, dict):
            return {'available': False, 'reason': 'no_recent_activity'}
        received_at = _number(item.get('received_at'))
        captured_at = _number(item.get('captured_at'))
        age = max(0, int(time.time() - received_at)) if received_at else 0
        if not received_at or age > self._mobile_activity_ttl():
            return {'available': False, 'reason': 'activity_expired', 'age_seconds': age}
        category = _clean(item.get('category'), 24).lower() or 'other'
        label = _clean(item.get('app_label'), MOBILE_ACTIVITY_MAX_LABEL)
        if any((marker in label.lower() for marker in MOBILE_ACTIVITY_LABEL_BLOCKLIST)):
            label = '其他应用'
        result = {'available': True, 'category': category if category in MOBILE_ACTIVITY_CATEGORIES else 'other', 'app_label': label or '其他应用', 'display_title': _clean(item.get('display_title'), MOBILE_ACTIVITY_MAX_LABEL) or label or '其他应用', 'captured_at': captured_at, 'last_report_at': received_at, 'age_seconds': age, 'stale': age > 5 * 60, 'source': 'android_usage_summary', 'privacy_mode': bool(item.get('privacy_mode', True)), 'music': {'playing': bool(item.get('music_playing')), 'source': _clean(item.get('music_source'), MOBILE_ACTIVITY_MAX_LABEL)}}
        if not prompt_safe:
            result['app_id'] = _clean(item.get('app_id'), 160)
        return result

    async def _notify_v2_location(self, user_id: str) -> None:
        try:
            getter = getattr(self, 'v2_host_api', None)
            api = getter() if callable(getter) else None
            notifier = getattr(api, 'notify_mobile_location_update', None) if api is not None else None
            if not callable(notifier):
                return
            result = notifier(user_id)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.debug('[RealityCompanion] 手机位置主动联动暂时失败: %s', _clean(exc, 160))

    def _schedule_v2_location_notification(self, user_id: str) -> None:
        normalized = _clean(user_id, 120)
        if not normalized:
            return
        previous = self._mobile_location_notify_tasks.get(normalized)
        if isinstance(previous, asyncio.Task) and (not previous.done()):
            self._mobile_location_notify_pending.add(normalized)
            return
        try:
            creator = getattr(self, '_create_lifecycle_background_task', None)
            task = creator(self._notify_v2_location(normalized), label='mobile_location_notify') if callable(creator) else asyncio.create_task(self._notify_v2_location(normalized))
        except RuntimeError:
            return
        self._mobile_location_notify_tasks[normalized] = task

        def clear(done: asyncio.Task) -> None:
            if self._mobile_location_notify_tasks.get(normalized) is done:
                self._mobile_location_notify_tasks.pop(normalized, None)
            if normalized in self._mobile_location_notify_pending:
                self._mobile_location_notify_pending.discard(normalized)
                self._schedule_v2_location_notification(normalized)
        task.add_done_callback(clear)

    def _mobile_screen_status(self, user_id: str) -> dict[str, Any]:
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            stored = self._mobile_screen_clients.get(_clean(user_id, 120))
            item = dict(stored) if isinstance(stored, dict) else None
        if not isinstance(item, dict):
            return {'available': self._mobile_screen_upload_enabled(), 'streaming': False,
                    'online': False, 'last_report_at': None, 'age_seconds': None,
                    'reason': 'no_recent_screen_heartbeat'}
        age = max(0, int(time.time() - _number(item.get('last_seen_at'))))
        return {'available': self._mobile_screen_upload_enabled(), 'streaming': age <= 90 and bool(item.get('streaming')),
                'online': self._mobile_screen_upload_enabled() and age <= 90,
                'client_id': _clean(item.get('client_id'), 100), 'age_seconds': age,
                'last_report_at': _number(item.get('last_seen_at'))}

    def _mobile_connection_status(self, user_id: str) -> dict[str, Any]:
        self._mobile_cleanup_sessions()
        now = time.time()
        with self._mobile_state_lock:
            sessions = [dict(item) for item in self._mobile_sessions.values()
                        if item.get('user_id') == user_id and self._mobile_snapshot_owner.session_authorized(item)]
        devices = []
        for session in sessions:
            last_seen = _number(session.get('last_seen_at'))
            age = max(0, int(now - last_seen)) if last_seen else None
            devices.append({'device_name': _clean(session.get('device_name'), 100),
                            'paired_at': _number(session.get('created_at')),
                            'last_seen_at': last_seen or None, 'age_seconds': age,
                            'online': age is not None and age <= 90,
                            'reports': {kind: {'accepted_at': received_at,
                                               'age_seconds': max(0, int(now - received_at))}
                                        for kind, received_at in session.get('report_receipts', {}).items()}})
        return {'owner': 'astrbot_plugin_private_companion', 'execution_path': 'v2_native',
                'active_sessions': len(devices), 'devices': devices}

    def _mobile_record_upload(self, session: dict[str, Any], kind: str, received_at: float) -> None:
        """Keep receipt timestamps with the source session, never payloads or tokens."""
        with self._mobile_state_lock:
            for current in self._mobile_sessions.values():
                if (current.get('snapshot_session_ref')
                        and current.get('snapshot_session_ref') == session.get('snapshot_session_ref')):
                    current.setdefault('report_receipts', {})[kind] = received_at
                    return

    def _mobile_device_snapshot(self, user_id: str) -> dict[str, Any]:
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            stored = self._mobile_device_status.get(_clean(user_id, 120))
            item = dict(stored) if isinstance(stored, dict) else None
        if not isinstance(item, dict):
            return {'available': False, 'reason': 'no_recent_device_status'}
        captured_at = _number(item.get('captured_at'))
        received_at = _number(item.get('received_at'))
        age = max(0, int(time.time() - received_at)) if received_at else 0
        if not captured_at or not received_at:
            return {'available': False, 'reason': 'invalid_device_status'}
        return {'available': True, 'online': age <= 5 * 60, 'device_name': _clean(item.get('device_name'), 120) or 'Android 设备', 'platform': _clean(item.get('platform'), 24).lower() or 'android', 'app_state': _clean(item.get('app_state'), 24) or 'unknown', 'battery_percent': int(max(0, min(100, _number(item.get('battery_percent'))))) if item.get('battery_percent') is not None else None, 'charging': bool(item.get('charging')), 'captured_at': captured_at, 'last_report_at': received_at, 'age_seconds': age, 'stale': age > 5 * 60, 'source': 'android_device_status'}

    def _mobile_telemetry_snapshot(self, user_id: str, *, prompt_safe: bool=False) -> dict[str, Any]:
        """Return a bounded, non-diagnostic projection of external measurements."""
        if not self._mobile_telemetry_enabled():
            return {'available': False, 'reason': 'telemetry_disabled'}
        self._mobile_cleanup_sessions()
        with self._mobile_state_lock:
            stored = self._mobile_telemetry.get(_clean(user_id, 120))
            item = dict(stored) if isinstance(stored, dict) else None
        if not isinstance(item, dict):
            return {'available': False, 'reason': 'no_recent_telemetry'}
        received_at = _number(item.get('received_at'))
        captured_at = _number(item.get('captured_at'))
        age = max(0, int(time.time() - received_at)) if received_at else 0
        if not received_at or age > self._mobile_telemetry_ttl():
            return {'available': False, 'reason': 'telemetry_expired', 'age_seconds': age}
        measurements: list[dict[str, Any]] = []
        now = time.time()
        for key, raw in (item.get('measurements') or {}).items():
            if not isinstance(raw, dict):
                continue
            name = _clean(key, 40).lower()
            value = _number(raw.get('value'), float('nan'))
            unit = _clean(raw.get('unit'), 16)
            sample_at = _number(raw.get('captured_at'), captured_at)
            if not name or not math.isfinite(value) or (not unit) or (not sample_at) or (sample_at - now > 10 * 60) or (now - sample_at > self._mobile_telemetry_ttl()):
                continue
            measurements.append({'type': name, 'value': round(value, 3), 'unit': unit, 'captured_at': sample_at, 'label': _telemetry_label(raw.get('label'))})
        measurements.sort(key=lambda entry: entry['type'])
        activity = item.get('activity') if isinstance(item.get('activity'), dict) else {}
        activity_state = _clean(activity.get('state'), 24).lower()
        activity_duration = _number(activity.get('duration_minutes'), 0.0)
        summary_parts: list[str] = []
        labels = {'heart_rate': '心率', 'steps': '步数', 'sleep_duration': '睡眠时长', 'active_calories': '活动消耗', 'body_temperature': '体温', 'oxygen_saturation': '血氧', 'weight': '体重', 'exercise_minutes': '运动时长'}
        for entry in measurements[:MOBILE_TELEMETRY_MAX_MEASUREMENTS]:
            label = entry.get('label') or labels.get(entry['type'], entry['type'])
            summary_parts.append(f"{label} {entry['value']} {entry['unit']}")
        if activity_state:
            activity_labels = {'walking': '步行', 'running': '跑步', 'cycling': '骑行', 'resting': '休息', 'sleeping': '睡眠', 'unknown': '活动状态未知'}
            activity_label = _telemetry_label(activity.get('label'))
            text = activity_label or activity_labels.get(activity_state, activity_state)
            if activity_duration > 0:
                text += f'（约 {round(activity_duration, 1)} 分钟）'
            summary_parts.append(f'当前活动：{text}')
        activity_result = {'type': _telemetry_identifier(activity.get('type') or activity_state, MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE), 'state': activity_state, 'label': activity_label if activity_state else '', 'duration_minutes': round(max(0.0, activity_duration), 1)}
        for key in ('started_at', 'ended_at'):
            timestamp = _number(activity.get(key))
            if timestamp:
                activity_result[key] = timestamp
        if isinstance(activity.get('metrics'), list):
            activity_result['metrics'] = [dict(metric) for metric in activity['metrics'] if isinstance(metric, dict)][:MOBILE_TELEMETRY_MAX_ACTIVITY_METRICS]
        events: list[dict[str, Any]] = []
        event_summaries: list[str] = []
        for raw_event in item.get('events') or []:
            if not isinstance(raw_event, dict):
                continue
            occurred_at = _number(raw_event.get('occurred_at'))
            if not occurred_at or occurred_at - now > 10 * 60 or now - occurred_at > 7 * 24 * 60 * 60:
                continue
            event = dict(raw_event)
            event['type'] = _telemetry_identifier(event.get('type'), MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE)
            event['label'] = _telemetry_label(event.get('label'))
            items = [dict(entry) for entry in event.get('items') or [] if isinstance(entry, dict) and _telemetry_label(entry.get('name'))][:MOBILE_TELEMETRY_MAX_EVENT_ITEMS]
            if items:
                event['items'] = items
            events.append(event)
            meal_labels = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐', 'snack': '加餐'}
            title = event.get('label') or meal_labels.get(event.get('meal_type'), '') or event.get('type') or '生活事件'
            details: list[str] = []
            for entry in items:
                item_text = _telemetry_label(entry.get('name'), 80)
                amount = entry.get('amount')
                unit = _clean(entry.get('unit'), 16)
                if isinstance(amount, (int, float)):
                    item_text += f" {amount:g}{(' ' + unit if unit else '')}"
                details.append(item_text)
            if event.get('calories') is not None:
                details.append(f"{event['calories']:g} kcal")
            event_summaries.append(f'{title}' + (f"（{'；'.join(details)}）" if details else ''))
        events.sort(key=lambda event: _number(event.get('occurred_at')), reverse=True)
        if event_summaries:
            summary_parts.append('近期事件：' + '；'.join(event_summaries[:8]))
        result = {'available': bool(measurements or activity_state), 'captured_at': captured_at, 'received_at': received_at, 'age_seconds': age, 'source': _clean(item.get('source'), 48) or 'external_telemetry', 'measurements': measurements, 'activity': activity_result, 'events': events, 'summary': '；'.join(summary_parts)}
        if prompt_safe:
            result['source'] = 'user_authorized_external_telemetry'
        return result

    async def _mobile_game_status(self, *, session_token: str='') -> dict[str, Any]:
        if not session_token:
            return {'available': False, 'ready': False, 'blockers': ['请先配对手机并绑定 V2 私聊目标'], 'games': []}
        try:
            result = self.v2_host_api().companion_v2_mobile_game_status(session_token)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.debug('[RealityCompanion] 读取 V2 手机游戏状态失败: %s', _clean(exc, 160))
            return {'available': False, 'ready': False, 'blockers': ['暂时无法确认此手机用户的游戏授权'], 'games': []}
        if type(result) is not dict or result.get('matched') is not True:
            return {'available': False, 'ready': False, 'blockers': ['请先在 V2 配置中绑定此手机用户的私聊目标'], 'games': []}
        labels = {'tictactoe': '井字棋', 'gomoku': '五子棋', 'xiangqi': '中国象棋',
                  'pig_dice': '掷骰竞分', 'blackjack': '二十一点', 'turtle_soup': '海龟汤', 'draw_guess': '你画我猜'}
        authorized = result.get('games') if type(result.get('games')) is list else []
        games = [{'game_type': 'native_v2:' + kind, 'label': labels[kind],
                  'description': '陪伴游戏房间', 'enabled': True, 'mode': 'native_v2'}
                 for kind in labels if kind in authorized]
        ready = bool(result.get('ready') and games)
        blockers = [] if ready else ['游戏房间未启用或此私聊目标尚未授权可用游戏']
        return {'available': bool(result.get('available')), 'enabled': bool(result.get('enabled')),
                'running': bool(result.get('running')), 'ready': ready, 'blockers': blockers, 'games': games}

    async def _mobile_status_response(self, auth: dict[str, Any], *, session_token: str='',
                                      discover_capabilities: bool=True) -> dict[str, Any]:
        user_id = _clean(auth.get('user_id'), 120)
        async def read_game_status() -> dict[str, Any]:
            try:
                return await asyncio.wait_for(
                    self._mobile_game_status(session_token=session_token),
                    timeout=MOBILE_STATUS_DISCOVERY_TIMEOUT_SECONDS)
            except Exception:
                return {'available': False, 'ready': False,
                        'blockers': ['暂时无法确认此手机用户的游戏授权'], 'games': []}

        async def read_together_status() -> dict[str, Any]:
            try:
                return (await asyncio.wait_for(
                    self._mobile_v2_together_status(session_token=session_token),
                    timeout=MOBILE_STATUS_DISCOVERY_TIMEOUT_SECONDS
                )) or {}
            except Exception:
                return {'matched': True, 'available': False, 'ready': False,
                        'capabilities': {}, 'modes': [], 'reason': 'mobile_v2_unavailable'}

        # Pairing should establish the session first; optional room/game
        # discovery must not serialize or indefinitely delay that response.
        if discover_capabilities:
            game_status, status = await asyncio.gather(read_game_status(), read_together_status())
        else:
            game_status = {'available': False, 'ready': False,
                           'blockers': ['连接完成后同步游戏状态'], 'games': []}
            status = {'matched': True, 'available': False, 'ready': False,
                      'capabilities': {}, 'modes': [], 'reason': 'mobile_status_pending'}
        matched = status.get('matched') is True
        available = matched and bool(status.get('available'))
        actions = status.get('capabilities') if type(status.get('capabilities')) is dict else {}
        modes = status.get('modes') if type(status.get('modes')) is list else []
        room_ready = bool(available and status.get('ready') and actions.get('create'))
        room_blockers = [] if room_ready else [self._mobile_together_blocker(status)]
        together_capabilities = {'chat': {'available': room_ready},
                                 'work': {'available': room_ready and 'work' in modes},
                                 'novel': {'available': room_ready and 'novel' in modes},
                                 'invite': {'available': available and bool(actions.get('invite'))},
                                 'join': {'available': available and bool(actions.get('join'))}}
        together_status = {'available': available, 'enabled': bool(status.get('enabled', status.get('available'))),
                           'running': available and bool(status.get('running')),
                           'execution': 'v2_native', 'ready': room_ready,
                           'reason': status.get('reason'), 'blockers': room_blockers, 'capabilities': together_capabilities}
        return {'ok': True, 'data': {'api_version': MOBILE_API_VERSION, 'paired': not bool(auth.get('pairing')), 'user_id': user_id, 'connections': self._mobile_connection_status(user_id), 'reality': self._mobile_reality_status(), 'location': self._mobile_location_snapshot(user_id), 'device': self._mobile_device_snapshot(user_id), 'activity': self._mobile_activity_snapshot(user_id), 'telemetry': self._mobile_telemetry_snapshot(user_id), 'screen': self._mobile_screen_status(user_id), 'together': together_status, 'games': game_status, 'capabilities': {'room': room_ready, 'call': room_ready and 'call' in modes, 'watch': room_ready and 'watch' in modes, 'novel': together_capabilities['novel']['available'], 'work': together_capabilities['work']['available'], 'invite': together_capabilities['invite']['available'], 'join': together_capabilities['join']['available'], 'location': True, 'telemetry': self._mobile_telemetry_enabled(), 'activity': self._mobile_activity_enabled(), 'screen_upload': self._mobile_screen_upload_enabled(), 'games': bool(game_status.get('ready'))}}}

    @staticmethod
    def _mobile_together_blocker(status: dict[str, Any]) -> str:
        messages = {
            'mobile_together_disabled': 'V2 内置一起的房间服务尚未启用',
            'mobile_together_not_authorized': '当前私聊目标尚未获得 V2 建房授权',
            'mobile_v2_together_start_failed': 'V2 内置一起的房间服务启动失败，请检查一起设置',
            'mobile_v2_provider_unavailable': 'V2 内置一起暂不可用，请检查本体运行状态',
        }
        if status.get('reason') in messages:
            return messages[status['reason']]
        if status.get('matched') is not True:
            return '请先将此手机用户绑定到 V2 的当前私聊目标'
        if not status.get('available'):
            return messages['mobile_together_disabled']
        if not status.get('capabilities', {}).get('create'):
            return messages['mobile_together_not_authorized']
        return 'V2 一起房间暂未就绪，请稍后重试'

    async def mobile_health(self) -> tuple[dict[str, Any], int]:
        """Return a deliberately small liveness response without credentials."""
        return ({'ok': True, 'data': {'service': 'reality_companion_mobile', 'api_version': MOBILE_API_VERSION, 'pairing_required': True,
                                    'owner': 'astrbot_plugin_private_companion', 'execution_path': 'v2_native',
                                    'gateway_version': PRODUCT_VERSION}}, 200)

    async def mobile_snapshot(self) -> tuple[dict[str, Any], int]:
        if not self._mobile_authorize():
            return self._mobile_json_error('未授权的移动端请求', 401)
        try:
            result = await self.v2_host_api().companion_v2_mobile_reality_snapshot(self._mobile_token_from_request())
        except asyncio.TimeoutError:
            return self._mobile_json_error('V2 手机摘要读取超时', 504)
        except Exception as error:
            body, status = self._mobile_json_error('V2 手机摘要暂不可读取，请检查当前私聊身份和摘要读取授权', 409)
            body['code'] = _clean(getattr(error, 'code', 'mobile_reality_snapshot_unavailable'), 100)
            return body, status
        return {'ok': True, 'data': result}, 200

    async def mobile_pair(self) -> tuple[dict[str, Any], int]:
        if self._mobile_pair_rate_limited():
            return self._mobile_json_error('配对尝试过于频繁，请稍后再试', 429)
        auth = self._mobile_authorize(allow_pairing=True)
        if not auth or not auth.get('pairing'):
            return self._mobile_json_error('配对令牌无效', 401)
        payload = await self._mobile_request_payload()
        auth = self._mobile_authorize(allow_pairing=True)
        if not auth or not auth.get('pairing'):
            return self._mobile_json_error('配对令牌无效', 401)
        requested_user = _clean(payload.get('user_id'), 120)
        configured_user = self._mobile_allowed_user_id()
        if not configured_user:
            return self._mobile_json_error('未配置可配对的主要用户 ID', 400)
        if requested_user and requested_user != configured_user:
            return self._mobile_json_error('请求绑定的用户与网关配置不一致', 403)
        user_id = configured_user
        context_getter = getattr(self, 'v2_host_api', None)
        try:
            api = context_getter() if callable(context_getter) else None
        except Exception as exc:
            logger.warning('[RealityCompanion] 获取 V2 移动端绑定上下文失败: %s', _clean(exc, 160))
            return self._mobile_json_error('暂时无法校验陪伴用户授权', 503)
        checker = getattr(api, 'reality_mobile_context', None) if api is not None else None
        if not callable(checker):
            return self._mobile_json_error('V2 未提供移动端身份校验', 503)
        try:
            host_context = checker(user_id)
        except Exception as exc:
            logger.warning('[RealityCompanion] 校验 V2 移动端绑定用户失败: %s', _clean(exc, 160))
            return self._mobile_json_error('暂时无法校验陪伴用户授权', 503)
        if (not isinstance(host_context, dict)
                or host_context.get('schema') != 'companion.reality-mobile-context.v1'
                or host_context.get('user_id') != user_id):
            return self._mobile_json_error('该用户没有可验证的 V2 私聊身份', 403)
        session_token = secrets.token_urlsafe(32)
        now = time.time()
        session = {'user_id': user_id, 'device_name': _clean(payload.get('device_name'), 100) or '陪伴终端 Android', 'created_at': now, 'last_seen_at': now, 'expires_at': now + self._mobile_session_ttl()}
        self._mobile_store_session(session_token, session)
        self._mobile_pair_rate_limited(succeeded=True)
        # Return the session immediately; the Android client fetches the full
        # capability status on its next request after pairing succeeds.
        result = await self._mobile_status_response(
            session, session_token=session_token, discover_capabilities=False)
        result['data']['session_token'] = session_token
        result['data']['session_expires_at'] = session['expires_at']
        logger.info('[RealityCompanion] Android 移动端已配对: user=%s device=%s', user_id, session['device_name'])
        return (result, 200)

    async def mobile_status(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        return (await self._mobile_status_response(auth, session_token=self._mobile_token_from_request()), 200)

    async def _mobile_call_gateway_aware(self, callback: Any, *args: Any) -> Any:
        """Call a cross-plugin API through the gateway when its wrapper permits it."""
        try:
            result = callback(*args, via_mobile_gateway=True)
        except TypeError as exc:
            message = str(exc).lower()
            if 'via_mobile_gateway' not in message and 'unexpected keyword' not in message:
                raise
            result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _mobile_prepare_together_access(self, together: Any) -> dict[str, Any]:
        """Prepare a room through the unified gateway, even for wrapped APIs."""
        access_preparer = getattr(together, '_ensure_mobile_room_access', None)
        if not callable(access_preparer):
            if self._mobile_proxy_rooms_enabled():
                room_server = getattr(together, 'room_server', None)
                starter = getattr(room_server, 'start', None)
                if callable(starter) and (not bool(getattr(room_server, 'running', False))):
                    await starter()
                local_url = str(getattr(room_server, 'local_base_url', '') or '')
                if local_url:
                    return {'url': local_url, 'tunnel_started': False, 'tunnel_ready': True, 'fixed_public_url': False, 'mobile_gateway': True}
            raise RuntimeError('一起房间插件版本过旧，缺少手机安全访问能力')
        try:
            result = await self._mobile_call_gateway_aware(access_preparer)
        except (RuntimeError, OSError) as exc:
            if not self._mobile_proxy_rooms_enabled():
                raise
            reason = _clean(exc, 240).lower()
            if not any((token in reason for token in ('cloudflared', 'cloudflare', 'cft', '公网访问', '公网地址'))):
                raise
            room_server = getattr(together, 'room_server', None)
            starter = getattr(room_server, 'start', None)
            if callable(starter) and (not bool(getattr(room_server, 'running', False))):
                await starter()
            local_url = str(getattr(room_server, 'local_base_url', '') or '')
            if not local_url:
                raise
            return {'url': local_url, 'tunnel_started': False, 'tunnel_ready': True, 'fixed_public_url': False, 'mobile_gateway': True}
        return result if isinstance(result, dict) else {}

    async def _mobile_v2_together_status(self, *, session_token: str='') -> dict[str, Any] | None:
        if not session_token:
            return {'matched': False, 'available': False, 'ready': False, 'capabilities': {}, 'modes': []}
        try:
            callback = self.v2_host_api().companion_v2_mobile_together_status
            result = await self._mobile_call_gateway_aware(callback, session_token)
        except Exception:
            return {'matched': True, 'available': False, 'ready': False, 'capabilities': {}, 'modes': [], 'reason': 'mobile_v2_unavailable'}
        if type(result) is not dict or type(result.get('matched')) is not bool:
            return {'matched': True, 'available': False, 'ready': False, 'capabilities': {}, 'modes': [], 'reason': 'mobile_v2_unavailable'}
        return result

    async def mobile_create_room(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        mode = _clean(payload.get('mode'), 16).lower()
        if mode not in {'call', 'watch', 'work', 'novel'}:
            mode = 'call'
        user_id = _clean(auth.get('user_id'), 120)
        together = self.native_extension('together')
        session_token = self._mobile_token_from_request()
        v2_status = await self._mobile_v2_together_status(session_token=session_token)
        if isinstance(v2_status, dict) and v2_status.get('matched') is True:
            capabilities = v2_status.get('capabilities') if isinstance(v2_status.get('capabilities'), dict) else {}
            modes = v2_status.get('modes') if isinstance(v2_status.get('modes'), list) else []
            if not v2_status.get('available') or not capabilities.get('create') or v2_status.get('ready') is False:
                body, code = self._mobile_json_error(self._mobile_together_blocker(v2_status), 409)
                body['code'] = v2_status.get('reason') or 'mobile_v2_together_not_ready'
                return body, code
            if mode not in modes:
                return self._mobile_json_error('当前 V2 一起拓展不支持所选房间模式', 409)
            if together is None:
                return self._mobile_json_error('V2 一起房间服务不可用', 503)
            if mode == 'work' and (not bool(together.work_collaboration_available())):
                return self._mobile_json_error('工作协同需要先启用屏幕伙伴', 409)
            request_id = payload.get('request_id')
            creator = getattr(self.v2_host_api(), 'companion_v2_mobile_together_create', None)
            if not callable(creator):
                return self._mobile_json_error('V2 一起房间入口不可用', 503)
            async with self._mobile_room_start_lock:
                try:
                    access = await self._mobile_prepare_together_access(together)
                    native_client = _clean(payload.get('client'), 40).lower() in {'android_native', 'android-native'}
                    access_base = self._mobile_external_origin() if self._mobile_proxy_rooms_enabled() else str(access.get('url') or '')
                    native_local_allowed = native_client and self._mobile_proxy_rooms_enabled()
                    if mode == 'call' and (not native_local_allowed) and (not access_base or urlsplit(access_base).scheme.lower() != 'https'):
                        return self._mobile_json_error('视频通话需要 HTTPS 安全地址，才能由手机浏览器安全申请摄像头和麦克风', 409)
                    result = await self._mobile_call_gateway_aware(creator, session_token, request_id, mode)
                except Exception as exc:
                    code = _clean(getattr(exc, 'code', ''), 100)
                    request_id = getattr(exc, 'request_id', request_id)
                    message = {
                        'mobile_together_result_unknown': '建房结果还在确认中，再点一次会继续检查这次请求',
                        'mobile_v2_context_changed': '私聊人格或会话已变化，请回到原会话确认房间状态',
                        'mobile_together_request_conflict': '这次请求的房间参数已变化，请使用原来的房间模式重试',
                        'mobile_request_id_required': '建房请求编号格式不正确，请更新手机客户端后重试',
                    }.get(code, 'V2 建房请求未完成')
                    logger.warning('[CompanionV2] 手机建房失败 code=%s error_type=%s',
                                   code or 'mobile_together_create_failed', type(exc).__name__)
                    body, status = self._mobile_json_error(message, 409)
                    body['code'] = code or 'mobile_together_create_failed'
                    if request_id:
                        body['data'] = {'request_id': request_id, 'execution_path': 'v2_native',
                                        'capability': 'together.room.create'}
                    if code == 'mobile_together_result_unknown' or not code:
                        body['uncertain'] = True
                    return body, status
            if type(result) is not dict or result.get('matched') is not True:
                return self._mobile_json_error('V2 建房目标已变化，请重新检查陪伴授权', 409)
            request_id = result.get('request_id', request_id)
            if result.get('state') != 'succeeded':
                uncertain = result.get('state') == 'unknown'
                body, status = self._mobile_json_error('建房结果还在确认中，再点一次会继续检查这次请求' if uncertain else 'V2 房间服务未能完成建房', 409)
                body['code'] = 'mobile_together_result_unknown' if uncertain else 'mobile_together_create_failed'
                body['data'] = {'request_id': request_id, 'execution_path': 'v2_native', 'capability': 'together.room.create',
                    **{key: result[key] for key in ('state', 'operation_id', 'receipt_ref', 'effect') if key in result}}
                body['uncertain'] = uncertain
                return (body, status)
            room = result.get('result', {}).get('room', {})
            room_url = self._mobile_room_url_via_gateway(str(room.get('url') or ''))
            native_client = _clean(payload.get('client'), 40).lower() in {'android_native', 'android-native'}
            if mode == 'call' and urlsplit(room_url).scheme.lower() != 'https' and (not (native_client and self._mobile_proxy_rooms_enabled())):
                return self._mobile_json_error('视频通话需要 HTTPS 安全地址，才能由手机浏览器安全申请摄像头和麦克风', 409)
            return ({'ok': True, 'data': {'url': room_url, 'mode': mode, 'expires_at': room.get('expires_at'),
                'request_id': request_id, 'execution_path': 'v2_native', 'capability': 'together.room.create',
                **{key: result.get(key) for key in ('state', 'effect', 'operation_id', 'receipt_ref')}}}, 200)
        return self._mobile_json_error('请先在 V2 配置中绑定此手机用户的私聊目标', 409)

    async def mobile_invite_room(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        session_token = self._mobile_token_from_request()
        status = await self._mobile_v2_together_status(session_token=session_token)
        capabilities = status.get('capabilities') if isinstance(status, dict) else {}
        if not isinstance(status, dict) or status.get('matched') is not True:
            return self._mobile_json_error('邀请功能需要已绑定 V2 私聊目标', 409)
        if not status.get('available') or not isinstance(capabilities, dict) or (not capabilities.get('invite')):
            return self._mobile_json_error('此 V2 目标尚未获得一起房间邀请授权', 403)
        together = self.native_extension('together')
        callback = getattr(self.v2_host_api(), 'companion_v2_mobile_together_invite', None)
        if together is None or not callable(callback):
            return self._mobile_json_error('V2 一起房间服务不可用', 503)
        request_id = payload.get('request_id')
        expires = payload.get('expires_in_seconds')
        if expires is not None and (type(expires) is not int or not 60 <= expires <= 3600):
            return self._mobile_json_error('邀请有效期必须在 60 到 3600 秒之间', 400)
        async with self._mobile_room_start_lock:
            try:
                await self._mobile_prepare_together_access(together)
                result = await self._mobile_call_gateway_aware(callback, session_token, request_id, expires)
            except Exception as exc:
                code = _clean(getattr(exc, 'code', ''), 100)
                message = '邀请结果暂不可确认，请使用原 request_id 查询或重试' if code == 'mobile_together_result_unknown' else 'V2 邀请请求未完成'
                return self._mobile_json_error(message, 409)
        if type(result) is not dict or result.get('matched') is not True:
            return self._mobile_json_error('V2 邀请目标已变化，请重新检查陪伴授权', 409)
        if result.get('state') != 'succeeded':
            uncertain = result.get('state') == 'unknown'
            body, status_code = self._mobile_json_error('邀请结果暂不可确认，请勿更换 request_id 重复提交' if uncertain else 'V2 服务未能发出邀请', 409)
            body['uncertain'] = uncertain
            body['data'] = {key: result[key] for key in ('operation_id', 'receipt_ref', 'effect') if key in result}
            return (body, status_code)
        invitation = result.get('result', {}).get('invitation', {})
        return ({'ok': True, 'data': {**invitation, 'request_id': request_id}}, 200)

    async def mobile_join_room(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        session_token = self._mobile_token_from_request()
        status = await self._mobile_v2_together_status(session_token=session_token)
        capabilities = status.get('capabilities') if isinstance(status, dict) else {}
        if not isinstance(status, dict) or status.get('matched') is not True:
            return self._mobile_json_error('加入功能需要已绑定 V2 私聊目标', 409)
        if not status.get('available') or not isinstance(capabilities, dict) or (not capabilities.get('join')):
            return self._mobile_json_error('此 V2 目标尚未获得一起房间加入授权', 403)
        invitation_id = _clean(payload.get('invitation_id'), 160)
        if not invitation_id:
            return self._mobile_json_error('必须提供 invitation_id', 400)
        together = self.native_extension('together')
        callback = getattr(self.v2_host_api(), 'companion_v2_mobile_together_join', None)
        if together is None or not callable(callback):
            return self._mobile_json_error('V2 一起房间服务不可用', 503)
        request_id = payload.get('request_id')
        native_client = _clean(payload.get('client'), 40).lower() in {'android_native', 'android-native'}
        async with self._mobile_room_start_lock:
            try:
                access = await self._mobile_prepare_together_access(together)
                access_base = self._mobile_external_origin() if self._mobile_proxy_rooms_enabled() else str(access.get('url') or '')
                native_local_allowed = native_client and self._mobile_proxy_rooms_enabled()
                if not native_local_allowed and (not access_base or urlsplit(access_base).scheme.lower() != 'https'):
                    return self._mobile_json_error('加入通话需要 HTTPS 安全地址', 409)
                result = await self._mobile_call_gateway_aware(callback, session_token, request_id, invitation_id)
            except Exception as exc:
                code = _clean(getattr(exc, 'code', ''), 100)
                message = '加入结果暂不可确认，请使用原 request_id 查询或重试' if code == 'mobile_together_result_unknown' else 'V2 加入请求未完成'
                return self._mobile_json_error(message, 409)
        if type(result) is not dict or result.get('matched') is not True:
            return self._mobile_json_error('V2 加入目标已变化，请重新检查陪伴授权', 409)
        if result.get('state') != 'succeeded':
            uncertain = result.get('state') == 'unknown'
            body, status_code = self._mobile_json_error('加入结果暂不可确认，请勿更换 request_id 重复提交' if uncertain else 'V2 服务未能加入此邀请', 409)
            body['uncertain'] = uncertain
            body['data'] = {key: result[key] for key in ('operation_id', 'receipt_ref', 'effect') if key in result}
            return (body, status_code)
        joined = result.get('result', {}).get('join', {})
        joined = dict(joined)
        joined['join_url'] = self._mobile_room_url_via_gateway(str(joined.get('join_url') or ''))
        return ({'ok': True, 'data': {**joined, 'request_id': request_id}}, 200)

    async def mobile_create_game_room(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        game = self.native_extension('game')
        if game is None:
            return self._mobile_json_error('V2 游戏模块未启用', 503)
        payload = await self._mobile_request_payload()
        try:
            response = await self.v2_host_api().companion_v2_mobile_game_create(
                self._mobile_token_from_request(), payload.get('request_id'), _clean(payload.get('game_type'), 40),
                via_mobile_gateway=True)
            if response.get('state') != 'succeeded':
                body, status_code = self._mobile_json_error('游戏结果暂不可确认，请保留原 request_id', 409)
                body['uncertain'] = response.get('state') == 'unknown'
                return body, status_code
            result = response['result']
        except (ValueError, PermissionError) as exc:
            return self._mobile_json_error(str(exc), 409)
        except (RuntimeError, OSError) as exc:
            return self._mobile_json_error(str(exc), 503)
        except Exception as exc:
            logger.exception('[RealityCompanion] 手机游戏房间创建失败: %s', _clean(exc, 180))
            return self._mobile_json_error('手机游戏房间创建失败', 500)
        if not isinstance(result, dict) or not result.get('url'):
            return self._mobile_json_error('游戏伴侣没有返回可用房间链接', 503)
        result = dict(result)
        result['url'] = self._mobile_room_url_via_gateway(str(result.get('url') or ''))
        return ({'ok': True, 'data': result}, 200)

    async def mobile_prepare_rooms(self) -> tuple[dict[str, Any], int]:
        """Warm mobile HTTPS room access without issuing a room ticket."""
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        together = self.native_extension('together')
        if together is None:
            return self._mobile_json_error('V2 共处模块未启用', 503)
        try:
            status = self.v2_host_api().companion_v2_mobile_together_status(self._mobile_token_from_request())
        except Exception:
            return self._mobile_json_error('V2 私聊目标或共处授权不可用', 409)
        if status.get('matched') is not True or not status.get('ready'):
            return self._mobile_json_error('请先配置 V2 私聊目标并授权创建共处房间', 409)
        async with self._mobile_room_start_lock:
            try:
                access = await self._mobile_prepare_together_access(together)
            except Exception as exc:
                return self._mobile_json_error(f'共同房间预热失败：{_clean(exc, 180)}', 503)
        return ({'ok': True, 'data': {'ready': bool(access.get('tunnel_ready', False)) if isinstance(access, dict) else True}}, 200)

    def _mobile_rewrite_room_url(self, room_url: str) -> str:
        """Replace loopback room hosts with the host used by the mobile request."""
        try:
            parsed = urlsplit(str(room_url or ''))
            if parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
                return room_url
            host_only = self._mobile_normalize_host(self._mobile_request_host())
            if not host_only:
                return room_url
            netloc = f'[{host_only}]' if ':' in host_only else host_only
            if parsed.port is not None:
                netloc = f'{netloc}:{parsed.port}'
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        except Exception:
            return room_url

    def _mobile_proxy_rooms_enabled(self) -> bool:
        return bool(self._cfg_bool('mobile.proxy_rooms', True))

    def _mobile_room_upstream(self, service: str) -> str:
        if service not in {'together', 'game'}:
            return ''
        plugin = self.native_extension(service)
        base = ''
        if plugin is not None:
            room_server = getattr(plugin, 'room_server', None)
            base = str(getattr(room_server, 'local_base_url', '') or '')
            if not base:
                getter = getattr(plugin, '_room_base_url', None)
                base = str(getter() or '') if callable(getter) else ''
        base = base.strip().rstrip('/')
        try:
            parsed = urlsplit(base)
            if parsed.hostname in {'0.0.0.0', ''}:
                netloc = '127.0.0.1' if parsed.port is None else f'127.0.0.1:{parsed.port}'
                base = urlunsplit((parsed.scheme or 'http', netloc, parsed.path, '', ''))
        except Exception:
            pass
        if base:
            self._mobile_room_upstream_cache[service] = base
        return base

    @staticmethod
    def _mobile_room_upstream_origin(upstream: str) -> str:
        """Return the origin that the local room service sees behind the proxy."""
        try:
            parsed = urlsplit(str(upstream or ''))
            if parsed.scheme and parsed.netloc:
                return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}'
        except Exception:
            pass
        return ''

    def _mobile_room_proxy_headers(self, aio_request: Any, upstream: str) -> dict[str, str]:
        """Forward browser headers while satisfying the upstream origin check."""
        forward_headers: dict[str, str] = {}
        for key in ('Range', 'Content-Type', 'Accept', 'Authorization', 'Referer', 'User-Agent', 'X-Together-Room', 'X-Together-Resume'):
            value = aio_request.headers.get(key)
            if value:
                forward_headers[key] = value
        upstream_origin = self._mobile_room_upstream_origin(upstream)
        if upstream_origin:
            forward_headers['Origin'] = upstream_origin
        return forward_headers

    def _mobile_room_url_via_gateway(self, room_url: str) -> str:
        """把房间链接改写为移动网关自身地址，路径与查询原样保留。"""
        fallback = self._mobile_rewrite_room_url(room_url)
        if not self._mobile_proxy_rooms_enabled():
            return fallback
        try:
            parsed = urlsplit(str(room_url or ''))
            if not parsed.path:
                return fallback
            external_origin = self._mobile_external_origin()
            if not external_origin:
                return fallback
            base = urlsplit(external_origin)
            path = f"{base.path.rstrip('/')}{parsed.path}"
            query = f'?{parsed.query}' if parsed.query else ''
            return f'{base.scheme}://{base.netloc}{path}{query}'
        except Exception:
            return fallback

    async def _mobile_room_proxy(self, aio_request: Any, service: str) -> Any:
        if web is None or aiohttp is None:
            return web.Response(status=503, text='aiohttp unavailable', content_type='text/plain')
        upstream = self._mobile_room_upstream(service)
        if not upstream:
            return web.Response(status=503, text=f'{service} 房间服务不可用', content_type='text/plain')
        target = upstream + aio_request.path
        if aio_request.query_string:
            target = f'{target}?{aio_request.query_string}'
        forward_headers = self._mobile_room_proxy_headers(aio_request, upstream)
        body = await aio_request.read() if aio_request.can_read_body else None
        session = self._mobile_proxy_session or aiohttp.ClientSession()
        owns_session = session is self._mobile_proxy_session
        try:
            async with session.request(aio_request.method, target, headers=forward_headers, data=body, allow_redirects=True) as response:
                if response.status >= 400:
                    error_body = await response.read()
                    return web.Response(status=response.status, body=error_body[:MOBILE_MAX_BODY_BYTES], content_type=response.content_type or 'text/plain')
                stream = web.StreamResponse(status=response.status, reason=response.reason)
                for header in ('Content-Type', 'Content-Range', 'Accept-Ranges', 'Cache-Control', 'Content-Disposition'):
                    value = response.headers.get(header)
                    if value:
                        stream.headers[header] = value
                await stream.prepare(aio_request)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    await stream.write(chunk)
                await stream.write_eof()
                return stream
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[RealityCompanion] 房间代理转发失败: service=%s path=%s error=%s', service, _clean(aio_request.path, 120), _clean(exc, 160))
            return web.Response(status=502, text='房间代理转发失败', content_type='text/plain')
        finally:
            if not owns_session:
                await session.close()

    async def _mobile_room_assets_proxy(self, aio_request: Any) -> Any:
        referer = str(aio_request.headers.get('Referer') or '')
        if '/room/' in referer or '/native/' in referer:
            return await self._mobile_room_proxy(aio_request, 'game')
        if '/join/' in referer:
            return await self._mobile_room_proxy(aio_request, 'together')
        first = await self._mobile_room_proxy(aio_request, 'together')
        if first.status < 400:
            return first
        second = await self._mobile_room_proxy(aio_request, 'game')
        return second if second.status < 400 else first

    async def _mobile_room_ws_proxy(self, aio_request: Any) -> Any:
        if web is None or aiohttp is None:
            return web.Response(status=503, text='aiohttp unavailable', content_type='text/plain')
        upstream = self._mobile_room_upstream('together')
        if not upstream:
            return web.Response(status=503, text='together 房间服务不可用', content_type='text/plain')
        server_ws = web.WebSocketResponse(heartbeat=20.0, autoping=True, max_msg_size=MOBILE_PROXY_MAX_WS_BYTES)
        await server_ws.prepare(aio_request)
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10.0))
        upstream_ws = None
        try:
            headers = {'Origin': upstream}
            for key, value in aio_request.headers.items():
                if key.lower().startswith('x-together'):
                    headers[key] = value
            target = f'{upstream}/ws'
            if aio_request.query_string:
                target = f'{target}?{aio_request.query_string}'
            upstream_ws = await session.ws_connect(target, headers=headers, heartbeat=20.0, max_msg_size=MOBILE_PROXY_MAX_WS_BYTES)

            async def pump(source: Any, sink: Any) -> None:
                async for message in source:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await sink.send_str(message.data)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        await sink.send_bytes(message.data)
                    else:
                        break
            tasks = [asyncio.create_task(pump(server_ws, upstream_ws)), asyncio.create_task(pump(upstream_ws, server_ws))]
            try:
                _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
            finally:
                for task in tasks:
                    task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[RealityCompanion] 房间 WS 代理失败: %s', _clean(exc, 160))
        finally:
            for closer in (upstream_ws, session):
                try:
                    if closer is not None:
                        await closer.close()
                except Exception:
                    pass
            try:
                await server_ws.close()
            except Exception:
                pass
        return server_ws

    async def mobile_location(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        latitude = _number(payload.get('latitude'), 999.0)
        longitude = _number(payload.get('longitude'), 999.0)
        if not (math.isfinite(latitude) and math.isfinite(longitude) and (-90.0 <= latitude <= 90.0) and (-180.0 <= longitude <= 180.0)):
            return self._mobile_json_error('经纬度不在有效范围内', 400)
        captured_at = _number(payload.get('captured_at'), time.time())
        if captured_at > 10000000000:
            captured_at /= 1000.0
        now = time.time()
        if not math.isfinite(captured_at) or abs(now - captured_at) > 10 * 60:
            return self._mobile_json_error('定位时间过旧，请重新获取位置', 400)
        user_id = _clean(auth.get('user_id'), 120)
        accuracy = _number(payload.get('accuracy_m'), 0.0)
        altitude = _number(payload.get('altitude_m')) if payload.get('altitude_m') is not None else None
        speed = _number(payload.get('speed_mps'), 0.0)
        bearing = _number(payload.get('bearing')) if payload.get('bearing') is not None else None
        raw_place = payload.get('place') if isinstance(payload.get('place'), dict) else {}
        place = {'matched': bool(raw_place.get('matched')), 'name': _clean(raw_place.get('name'), 40), 'kind': _clean(raw_place.get('kind'), 24), 'distance_m': max(0.0, min(100000.0, _number(raw_place.get('distance_m')))), 'radius_m': max(20.0, min(5000.0, _number(raw_place.get('radius_m'), 150.0))), 'confidence': _clean(raw_place.get('confidence'), 32), 'aliases': [_clean(item, 40) for item in list(raw_place.get('aliases') or [])[:8] if _clean(item, 40)], 'parent_name': _clean(raw_place.get('parent_name'), 40)}
        item = {'latitude': latitude, 'longitude': longitude, 'accuracy_m': max(0.0, min(100000.0, accuracy)) if math.isfinite(accuracy) else 0.0, 'altitude_m': altitude if altitude is not None and math.isfinite(altitude) else None, 'speed_mps': max(0.0, min(1000.0, speed)) if math.isfinite(speed) else 0.0, 'bearing': bearing % 360.0 if bearing is not None and math.isfinite(bearing) else None, 'label': _clean(payload.get('label'), 40), 'place': place, 'captured_at': captured_at, 'received_at': now}
        with self._mobile_state_lock:
            auth = self._mobile_authorize()
            if not auth:
                return self._mobile_json_error('未授权的移动端请求', 401)
            self._mobile_snapshot_owner.stamp(user_id, 'location', item, session=auth)
            self._mobile_locations[user_id] = item
        self._schedule_mobile_amap_enrichment(user_id, item)
        self._schedule_v2_location_notification(user_id)
        return ({'ok': True, 'data': self._mobile_location_snapshot(user_id)}, 200)

    def _mobile_persona_read(self, user_id: str) -> dict[str, Any]:
        """Use this phone's alias and its current V2 persona identity."""
        user = self._user(user_id, create=False) or {}
        alias = _clean(user.get('mobile_bot_name'), 12)
        if alias:
            return {'bot_name': alias, 'source': 'v2_mobile_alias'}
        try:
            resolved = self.v2_host_api().mobile_target_context(user_id)
            if resolved is None:
                return {'bot_name': '', 'source': 'unavailable'}
            session, application, context = resolved
            from companion_v2.role_profile.context import ConversationRoleProfileContext
            profile = ConversationRoleProfileContext(application.runtime).capture(context, self.controller.now())
            name = profile['fields'].get('name') if profile else ''
            if not name:
                personas = getattr(getattr(session, 'bridge', None), 'personas', ())
                personas = personas.values() if isinstance(personas, dict) else personas
                persona = next((item for item in personas if item.role == context.role_binding.role), None)
                name = getattr(persona, 'persona_id', '')
            return {'bot_name': _clean(name, 12), 'source': 'v2_persona'}
        except Exception:
            return {'bot_name': '', 'source': 'unavailable'}

    async def mobile_get_persona(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        return ({'ok': True, **self._mobile_persona_read(_clean(auth.get('user_id'), 120))}, 200)

    async def mobile_set_persona(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        name = payload.get('bot_name')
        if type(name) is not str or not name.strip() or len(name.strip()) > 12 or any(char in name for char in '\r\n\x00'):
            return self._mobile_json_error('称呼应为 1 到 12 个字符', 400)
        user_id = _clean(auth.get('user_id'), 120)
        user = self._user(user_id, create=True)
        user['mobile_bot_name'] = name.strip()
        await asyncio.to_thread(self._save_data_sync)
        return ({'ok': True, 'bot_name': name.strip(), 'source': 'v2_mobile_alias'}, 200)

    async def mobile_device_status(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        payload = await self._mobile_request_payload()
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        app_state = _clean(payload.get('app_state'), 24).lower()
        if app_state not in {'foreground', 'background'}:
            return self._mobile_json_error('应用状态无效', 400)
        captured_at = _number(payload.get('captured_at'), time.time())
        if captured_at > 10000000000:
            captured_at /= 1000.0
        now = time.time()
        if not math.isfinite(captured_at) or abs(now - captured_at) > 10 * 60:
            return self._mobile_json_error('设备状态时间过旧，请重新上报', 400)
        raw_battery = payload.get('battery_percent')
        battery = _number(raw_battery) if raw_battery is not None else None
        if battery is not None and (not math.isfinite(battery) or not 0 <= battery <= 100):
            return self._mobile_json_error('电量百分比无效', 400)
        user_id = _clean(auth.get('user_id'), 120)
        item = {'device_name': _clean(payload.get('device_name'), 120), 'platform': _clean(payload.get('platform'), 24).lower() or 'android', 'app_state': app_state, 'battery_percent': round(battery, 1) if battery is not None else None, 'charging': bool(payload.get('charging')), 'captured_at': captured_at, 'received_at': now}
        with self._mobile_state_lock:
            auth = self._mobile_authorize()
            if not auth:
                return self._mobile_json_error('未授权的移动端请求', 401)
            self._mobile_snapshot_owner.stamp(user_id, 'device', item, session=auth)
            self._mobile_device_status[user_id] = item
        return ({'ok': True, 'data': self._mobile_device_snapshot(user_id)}, 200)

    async def mobile_device_activity(self) -> tuple[dict[str, Any], int]:
        """Accept a consented, coarse foreground-app summary from Android."""
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        if not self._mobile_activity_enabled():
            return self._mobile_json_error('手机活动摘要未在配置中启用', 409)
        payload = await self._mobile_request_payload()
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        if payload.get('consent') is not True:
            return self._mobile_json_error('上报手机活动前需要明确同意', 409)
        captured_at = _number(payload.get('captured_at'), time.time())
        if captured_at > 10000000000:
            captured_at /= 1000.0
        now = time.time()
        if not math.isfinite(captured_at) or abs(now - captured_at) > 10 * 60:
            return self._mobile_json_error('活动时间过旧，请重新上报', 400)
        app_id = _clean(payload.get('app_id'), 160).lower()
        app_label = _clean(payload.get('app_label'), MOBILE_ACTIVITY_MAX_LABEL)
        display_title = _clean(payload.get('display_title') or app_label, MOBILE_ACTIVITY_MAX_LABEL)
        category = _clean(payload.get('category'), 24).lower()
        privacy_mode = payload.get('privacy_mode') is not False
        music_playing = bool(payload.get('music_playing'))
        music_source = _clean(payload.get('music_source'), MOBILE_ACTIVITY_MAX_LABEL)
        if category not in MOBILE_ACTIVITY_CATEGORIES:
            category = 'other'
        if not app_id and (not app_label):
            return self._mobile_json_error('活动摘要缺少应用信息', 400)
        marker_text = f'{app_id} {app_label}'.lower()
        if privacy_mode and any((marker in marker_text for marker in MOBILE_PRIVATE_APP_MARKERS)):
            app_id = ''
            app_label = '私密应用'
            display_title = '私密应用'
            category = 'private'
            music_source = ''
        if any((marker in display_title.lower() for marker in MOBILE_ACTIVITY_LABEL_BLOCKLIST)):
            display_title = '其他应用'
        if category != 'music':
            music_source = ''
        item = {'app_id': app_id, 'app_label': app_label or '其他应用', 'display_title': display_title or app_label or '其他应用', 'category': category, 'app_state': _clean(payload.get('app_state'), 24).lower() or 'foreground', 'privacy_mode': privacy_mode, 'music_playing': music_playing, 'music_source': music_source, 'captured_at': captured_at, 'received_at': now}
        user_id = _clean(auth.get('user_id'), 120)
        with self._mobile_state_lock:
            auth = self._mobile_authorize()
            if not auth:
                return self._mobile_json_error('未授权的移动端请求', 401)
            self._mobile_snapshot_owner.stamp(user_id, 'activity', item, session=auth)
            self._mobile_activity[user_id] = item
        return ({'ok': True, 'data': self._mobile_activity_snapshot(user_id)}, 200)

    @staticmethod
    def _normalize_telemetry_timestamp(value: Any, fallback: float, now: float, *, max_age: float=10 * 60) -> float:
        timestamp = _number(value, fallback)
        if timestamp > 10000000000:
            timestamp /= 1000.0
        if not math.isfinite(timestamp) or abs(now - timestamp) > max_age:
            return 0.0
        return timestamp

    def _normalize_telemetry_measurements(self, raw_measurements: Any, captured_at: float, now: float, *, limit: int=MOBILE_TELEMETRY_MAX_MEASUREMENTS) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_measurements, list):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for raw in raw_measurements[:limit]:
            if not isinstance(raw, dict):
                continue
            name = _telemetry_identifier(raw.get('type'), MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE)
            unit = _clean(raw.get('unit'), 16)
            value = _number(raw.get('value'), float('nan'))
            if not name or not unit or (not unit.isascii()) or (not all((char.isalnum() or char in '%/_-' for char in unit))) or (not math.isfinite(value)) or (abs(value) > 1000000000):
                continue
            if len(normalized) >= MOBILE_TELEMETRY_MAX_TYPES and name not in normalized:
                continue
            sample_time = self._normalize_telemetry_timestamp(raw.get('captured_at'), captured_at, now)
            if not sample_time:
                continue
            previous = normalized.get(name)
            if previous is None or sample_time >= _number(previous.get('captured_at')):
                sample = {'type': name, 'value': round(value, 3), 'unit': unit, 'captured_at': sample_time}
                label = _telemetry_label(raw.get('label'))
                if label:
                    sample['label'] = label
                normalized[name] = sample
        return normalized

    def _normalize_telemetry_events(self, raw_events: Any, captured_at: float, now: float) -> list[dict[str, Any]]:
        if not isinstance(raw_events, list):
            return []
        events: list[dict[str, Any]] = []
        for raw in raw_events[:MOBILE_TELEMETRY_MAX_EVENTS]:
            if not isinstance(raw, dict):
                continue
            event_type = _telemetry_identifier(raw.get('type'), MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE)
            if not event_type:
                continue
            occurred_at = self._normalize_telemetry_timestamp(raw.get('occurred_at'), captured_at, now, max_age=7 * 24 * 60 * 60)
            if not occurred_at:
                continue
            event: dict[str, Any] = {'type': event_type, 'occurred_at': occurred_at}
            for key in ('event_id', 'category', 'meal_type'):
                value = _telemetry_identifier(raw.get(key), 48)
                if value:
                    event[key] = value
            label = _telemetry_label(raw.get('label'))
            if label:
                event['label'] = label
            for key in ('started_at', 'ended_at'):
                if key not in raw:
                    continue
                timestamp = self._normalize_telemetry_timestamp(raw.get(key), occurred_at, now, max_age=7 * 24 * 60 * 60)
                if timestamp:
                    event[key] = timestamp
            duration = _number(raw.get('duration_minutes'), float('nan'))
            if math.isfinite(duration) and 0 <= duration <= 1440:
                event['duration_minutes'] = round(duration, 1)
            calories = _number(raw.get('calories'), float('nan'))
            if math.isfinite(calories) and 0 <= calories <= 1000000:
                event['calories'] = round(calories, 1)
            items: list[dict[str, Any]] = []
            raw_items = raw.get('items')
            if isinstance(raw_items, list):
                for item in raw_items[:MOBILE_TELEMETRY_MAX_EVENT_ITEMS]:
                    if not isinstance(item, dict):
                        continue
                    name = _telemetry_label(item.get('name'), 80)
                    if not name:
                        continue
                    normalized_item: dict[str, Any] = {'name': name}
                    amount = _number(item.get('amount'), float('nan'))
                    if math.isfinite(amount) and 0 <= amount <= 1000000:
                        normalized_item['amount'] = round(amount, 3)
                    unit = _clean(item.get('unit'), 16)
                    if unit and unit.isascii() and all((char.isalnum() or char in '%/_-' for char in unit)):
                        normalized_item['unit'] = unit
                    item_calories = _number(item.get('calories'), float('nan'))
                    if math.isfinite(item_calories) and 0 <= item_calories <= 1000000:
                        normalized_item['calories'] = round(item_calories, 1)
                    items.append(normalized_item)
            if items:
                event['items'] = items
            metrics = self._normalize_telemetry_measurements(raw.get('metrics'), occurred_at, now, limit=MOBILE_TELEMETRY_MAX_EVENT_METRICS)
            if metrics:
                event['metrics'] = list(metrics.values())
            if len(event) <= 2:
                event['label'] = event_type
            events.append(event)
        return events

    def _normalize_mobile_telemetry_payload(self, payload: dict[str, Any], *, now: float | None=None) -> dict[str, Any] | None:
        now = float(now or time.time())
        captured_at = self._normalize_telemetry_timestamp(payload.get('captured_at'), now, now)
        if not captured_at:
            return None
        raw_measurements = payload.get('measurements')
        if not isinstance(raw_measurements, list) or len(raw_measurements) > MOBILE_TELEMETRY_MAX_MEASUREMENTS:
            return None
        normalized = self._normalize_telemetry_measurements(raw_measurements, captured_at, now)
        raw_events = payload.get('events')
        if raw_events is not None and (not isinstance(raw_events, list) or len(raw_events) > MOBILE_TELEMETRY_MAX_EVENTS):
            return None
        events = self._normalize_telemetry_events(raw_events, captured_at, now)
        activity_payload = payload.get('activity') if isinstance(payload.get('activity'), dict) else {}
        activity_type = _telemetry_identifier(activity_payload.get('type') or activity_payload.get('state'), MOBILE_TELEMETRY_MAX_ACTIVITY_TYPE)
        activity_label = _telemetry_label(activity_payload.get('label'))
        duration = _number(activity_payload.get('duration_minutes'), 0.0)
        if not math.isfinite(duration) or not 0 <= duration <= 1440:
            duration = 0.0
        activity: dict[str, Any] = {'type': activity_type, 'state': activity_type, 'label': activity_label, 'duration_minutes': round(duration, 1)}
        for key in ('started_at', 'ended_at'):
            if key not in activity_payload:
                continue
            timestamp = self._normalize_telemetry_timestamp(activity_payload.get(key), now, now, max_age=7 * 24 * 60 * 60)
            if timestamp:
                activity[key] = timestamp
        activity_metrics = self._normalize_telemetry_measurements(activity_payload.get('metrics'), captured_at, now, limit=MOBILE_TELEMETRY_MAX_ACTIVITY_METRICS)
        if activity_metrics:
            activity['metrics'] = list(activity_metrics.values())
            normalized.update({f'activity.{key}': value for key, value in activity_metrics.items()})
        if not normalized and (not activity_type) and (not events):
            return None
        source = _telemetry_identifier(payload.get('source'), 48) or 'external_telemetry'
        return {'captured_at': captured_at, 'source': source, 'measurements': normalized, 'activity': activity, 'events': events}

    def _store_mobile_telemetry(self, user_id: str, normalized: dict[str, Any], *, received_at: float | None=None, session: dict[str, Any] | None=None) -> dict[str, Any]:
        user_id = _clean(user_id, 120)
        now = float(received_at or time.time())
        with self._mobile_state_lock:
            previous = self._mobile_telemetry.get(user_id)
            merged = dict(previous) if isinstance(previous, dict) else {}
            measurements = dict(merged.get('measurements') or {})
            for name, sample in (normalized.get('measurements') or {}).items():
                old = measurements.get(name)
                if not isinstance(old, dict) or _number(sample.get('captured_at')) >= _number(old.get('captured_at')):
                    measurements[name] = sample
            events_by_key: dict[str, dict[str, Any]] = {}
            for event in merged.get('events') or []:
                if not isinstance(event, dict):
                    continue
                key = _telemetry_identifier(event.get('event_id'), 48) or '|'.join((_telemetry_identifier(event.get('type'), 40), str(round(_number(event.get('occurred_at')), 1)), _telemetry_label(event.get('label'), 80)))
                if key:
                    events_by_key[key] = event
            for event in normalized.get('events') or []:
                if not isinstance(event, dict):
                    continue
                key = _telemetry_identifier(event.get('event_id'), 48) or '|'.join((_telemetry_identifier(event.get('type'), 40), str(round(_number(event.get('occurred_at')), 1)), _telemetry_label(event.get('label'), 80)))
                if key:
                    events_by_key[key] = event
            events = sorted(events_by_key.values(), key=lambda event: _number(event.get('occurred_at')), reverse=True)[:MOBILE_TELEMETRY_MAX_EVENTS]
            merged.update({'captured_at': max(_number(normalized.get('captured_at'), now), _number(merged.get('captured_at'), now)), 'received_at': now, 'source': normalized.get('source') or 'external_telemetry', 'measurements': measurements, 'activity': normalized.get('activity') or {}, 'events': events})
            self._mobile_snapshot_owner.stamp(user_id, 'telemetry', merged, session=session, previous=previous)
            self._mobile_telemetry[user_id] = merged
        return self._mobile_telemetry_snapshot(user_id)

    async def mobile_telemetry(self) -> tuple[dict[str, Any], int]:
        """Accept a bounded, structured snapshot from an authorized external app."""
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        if not self._mobile_telemetry_enabled():
            return self._mobile_json_error('身体数据接收未在配置中启用', 409)
        normalized = self._normalize_mobile_telemetry_payload(await self._mobile_request_payload())
        if normalized is None:
            return self._mobile_json_error('没有可用的结构化数据，或数据时间已过期', 400)
        with self._mobile_state_lock:
            auth = self._mobile_authorize()
            if not auth:
                return self._mobile_json_error('未授权的移动端请求', 401)
            return ({'ok': True, 'data': self._store_mobile_telemetry(auth.get('user_id'), normalized, session=auth)}, 200)

    async def mobile_screen_heartbeat(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        if not self._mobile_screen_upload_enabled():
            return self._mobile_json_error('手机屏幕共享上报已在配置中关闭', 409)
        payload = await self._mobile_request_payload()
        user_id = _clean(auth.get('user_id'), 120)
        with self._mobile_state_lock:
            auth = self._mobile_authorize()
            if not auth:
                return self._mobile_json_error('未授权的移动端请求', 401)
            now = time.time()
            self._mobile_screen_clients[user_id] = {'client_id': _clean(payload.get('client_id'), 100),
                'streaming': bool(payload.get('streaming')), 'last_seen_at': now,
                'session_ref': auth['snapshot_session_ref']}
            self._mobile_record_upload(auth, 'screen', now)
        return ({'ok': True, 'data': self._mobile_screen_status(user_id)}, 200)

    async def mobile_location_heartbeat(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        user_id = _clean(auth.get('user_id'), 120)
        with self._mobile_state_lock:
            item = self._mobile_locations.get(user_id)
            if not item:
                return self._mobile_json_error('当前没有可保活的位置，请重新获取定位', 409)
            item['received_at'] = time.time()
        return ({'ok': True, 'data': self._mobile_location_snapshot(user_id)}, 200)

    async def mobile_revoke_location(self) -> tuple[dict[str, Any], int]:
        auth = self._mobile_authorize()
        if not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        with self._mobile_state_lock:
            self._mobile_locations.pop(_clean(auth.get('user_id'), 120), None)
            self._mobile_snapshot_owner.invalidate(_clean(auth.get('user_id'), 120), ('location',))
        return ({'ok': True, 'data': {'revoked': True}}, 200)

    async def mobile_close_session(self) -> tuple[dict[str, Any], int]:
        token = self._mobile_token_from_request()
        auth = self._mobile_authorize()
        if not token or not auth:
            return self._mobile_json_error('未授权的移动端请求', 401)
        with self._mobile_state_lock:
            self._mobile_sessions.pop(self._mobile_token_key(token), None)
            self._mobile_snapshot_owner.session_changed(_clean(auth.get('user_id'), 120))
        return ({'ok': True, 'data': {'closed': True}}, 200)

    @staticmethod
    def _mobile_aiohttp_response(result: Any) -> Any:
        body: dict[str, Any]
        status = 200
        if isinstance(result, tuple) and len(result) == 2:
            candidate, candidate_status = result
            body = candidate if isinstance(candidate, dict) else {'ok': False, 'message': '移动端响应格式无效'}
            try:
                status = int(candidate_status)
            except (TypeError, ValueError):
                status = 500
        elif isinstance(result, dict):
            body = result
        else:
            body = {'ok': False, 'message': '移动端响应格式无效'}
            status = 500
        return web.json_response(body, status=max(100, min(599, status)), headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'})

    async def _mobile_aiohttp_dispatch(self, aio_request: Any, handler: Any) -> Any:
        if web is None:
            raise RuntimeError('aiohttp 不可用')
        payload: dict[str, Any] = {}
        if aio_request.can_read_body:
            content_length = aio_request.content_length
            if content_length is not None and content_length > MOBILE_MAX_BODY_BYTES:
                return self._mobile_aiohttp_response(self._mobile_json_error('请求体过大', 413))
            try:
                candidate = await aio_request.json()
            except Exception:
                return self._mobile_aiohttp_response(self._mobile_json_error('请求体必须是 JSON 对象', 400))
            if not isinstance(candidate, dict):
                return self._mobile_aiohttp_response(self._mobile_json_error('请求体必须是 JSON 对象', 400))
            payload = candidate
        transport = getattr(aio_request, 'transport', None)
        socket_name = transport.get_extra_info('sockname') if transport is not None else None
        local_host = str(socket_name[0]) if isinstance(socket_name, tuple) and socket_name else ''
        state = _MobileRequestState(headers={str(key).lower(): str(value) for key, value in aio_request.headers.items()}, host=local_host, remote_addr=str(aio_request.remote or ''), payload=payload)
        context_token = _mobile_request_state.set(state)
        try:
            result = await handler()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception('[RealityCompanion] 移动端 API 处理失败: %s', _clean(exc, 180))
            result = self._mobile_json_error('移动端服务处理失败', 500)
        finally:
            _mobile_request_state.reset(context_token)
        report_handlers = {'mobile_location', 'mobile_location_heartbeat', 'mobile_device_status',
                           'mobile_device_activity', 'mobile_telemetry', 'mobile_screen_heartbeat'}
        operation = getattr(handler, '__name__', '')
        if operation in report_handlers and isinstance(result, tuple):
            body, status = result
            accepted = isinstance(body, dict) and body.get('ok') is True and status == 200
            log = logger.info if accepted else logger.warning
            log('[CompanionV2] 手机上报 operation=%s status=%s accepted=%s', operation, status, accepted)
        return self._mobile_aiohttp_response(result)

    async def _start_mobile_server(self) -> bool:
        self._mobile_runtime_stopped = False
        if not self._mobile_enabled():
            return False
        if self._mobile_server_runner is not None:
            return True
        if web is None:
            self._mobile_runtime_stopped = True
            logger.error('[RealityCompanion] 移动端网关已启用，但缺少 aiohttp 依赖')
            return False
        if not self._mobile_pairing_token():
            self._mobile_runtime_stopped = True
            logger.error('[RealityCompanion] 移动端网关未启动：请先配置 mobile.pairing_token')
            return False
        if len(self._mobile_pairing_token()) < 24:
            logger.warning('[RealityCompanion] mobile.pairing_token 较短，建议改用至少 24 位随机令牌')
        prefix = f'/{PLUGIN_NAME}/mobile'
        app = web.Application(client_max_size=MOBILE_MAX_BODY_BYTES)
        canonical_routes = (('GET', '/health', self.mobile_health), ('POST', '/pair', self.mobile_pair), ('GET', '/status', self.mobile_status), ('GET', '/persona', self.mobile_get_persona), ('POST', '/persona', self.mobile_set_persona), ('POST', '/room/create', self.mobile_create_room), ('POST', '/room/invite', self.mobile_invite_room), ('POST', '/room/join', self.mobile_join_room), ('POST', '/game/room/create', self.mobile_create_game_room), ('POST', '/room/prepare', self.mobile_prepare_rooms), ('POST', '/location', self.mobile_location), ('POST', '/location/heartbeat', self.mobile_location_heartbeat), ('POST', '/location/revoke', self.mobile_revoke_location), ('POST', '/device/status', self.mobile_device_status), ('POST', '/device/activity', self.mobile_device_activity), ('POST', '/telemetry', self.mobile_telemetry), ('POST', '/screen/heartbeat', self.mobile_screen_heartbeat), ('POST', '/session/close', self.mobile_close_session))
        legacy_prefixes = (prefix, '/astrbot_plugin_reality_companion/mobile')
        routes = canonical_routes + tuple(
            (method, f'{route_prefix}{path}', handler)
            for route_prefix in legacy_prefixes
            for method, path, handler in canonical_routes
        )
        for method, path, handler in routes:
            app.router.add_route(method, path, functools.partial(self._mobile_aiohttp_dispatch, handler=handler))
        for path in ('/snapshot', *(f'{route_prefix}/snapshot' for route_prefix in legacy_prefixes)):
            app.router.add_route('GET', path, functools.partial(self._mobile_aiohttp_dispatch, handler=self.mobile_snapshot))
        if self._mobile_proxy_rooms_enabled() and aiohttp is not None:
            proxy_routes = (('*', '/join/{tail:.*}', functools.partial(self._mobile_room_proxy, service='together')), ('GET', '/media/{tail:.*}', functools.partial(self._mobile_room_proxy, service='together')), ('GET', '/avatar', functools.partial(self._mobile_room_proxy, service='together')), ('*', '/api/novels/{tail:.*}', functools.partial(self._mobile_room_proxy, service='together')), ('*', '/assets/{name}', self._mobile_room_assets_proxy), ('GET', '/native-assets/{name}', functools.partial(self._mobile_room_proxy, service='game')), ('*', '/api/room/{tail:.*}', functools.partial(self._mobile_room_proxy, service='game')), ('*', '/api/native/{tail:.*}', functools.partial(self._mobile_room_proxy, service='game')), ('GET', '/room/{access_token}', functools.partial(self._mobile_room_proxy, service='game')), ('GET', '/native/{access_token}', functools.partial(self._mobile_room_proxy, service='game')), ('GET', '/ws', self._mobile_room_ws_proxy))
            for method, path, handler in proxy_routes:
                app.router.add_route(method, path, handler)
            self._mobile_proxy_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10.0))
            logger.info('[RealityCompanion] 移动网关已启用统一房间代理')
        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host=self._mobile_host(), port=self._mobile_port())
            await site.start()
        except Exception as exc:
            try:
                await runner.cleanup()
            except Exception as cleanup_exc:
                logger.debug('[RealityCompanion] 移动端网关失败后的清理异常: %s', _clean(cleanup_exc, 160))
            self._mobile_runtime_stopped = True
            logger.error('[RealityCompanion] 移动端网关启动失败: host=%s port=%s error=%s', self._mobile_host(), self._mobile_port(), _clean(exc, 220))
            return False
        self._mobile_server_runner = runner
        self._mobile_server_site = site
        self._mobile_server_bound_port = self._mobile_port()
        server = getattr(site, '_server', None)
        sockets = getattr(server, 'sockets', None)
        if sockets:
            try:
                self._mobile_server_bound_port = int(sockets[0].getsockname()[1])
            except (AttributeError, IndexError, TypeError, ValueError):
                pass
        logger.info('[RealityCompanion] Android 移动端网关已监听: http://%s:%s', self._mobile_host(), self._mobile_server_bound_port)
        return True

    async def _stop_mobile_server(self) -> None:
        site = self._mobile_server_site
        runner = self._mobile_server_runner
        self._mobile_server_site = None
        self._mobile_server_runner = None
        self._mobile_server_bound_port = 0
        self._mobile_runtime_stopped = True
        self._mobile_snapshot_owner.rotate()
        session = self._mobile_proxy_session
        self._mobile_proxy_session = None
        try:
            if session is not None:
                await session.close()
        except Exception:
            pass
        try:
            if site is not None:
                await site.stop()
        finally:
            try:
                if runner is not None:
                    await runner.cleanup()
            finally:
                with self._mobile_state_lock:
                    self._mobile_sessions.clear()
                    self._mobile_locations.clear()
                    self._mobile_device_status.clear()
                    self._mobile_activity.clear()
                    self._mobile_telemetry.clear()
                    self._mobile_screen_clients.clear()
                    self._mobile_pair_attempts.clear()
                    self._mobile_location_notify_pending.clear()
                for task in list(self._mobile_location_enrich_tasks.values()):
                    if not task.done():
                        task.cancel()
                self._mobile_location_enrich_tasks.clear()

    def _register_mobile_api(self) -> None:
        register_api = getattr(self.context, 'register_web_api', None)
        if not callable(register_api):
            logger.warning('[RealityCompanion] 当前 AstrBot 不支持移动端网关 API')
            return

        def route(handler):

            @functools.wraps(handler)
            async def wrapped(*args, **kwargs):
                return await handler(*args, **kwargs)
            return wrapped
        prefixes = (f'/{PLUGIN_NAME}/mobile', '/astrbot_plugin_reality_companion/mobile')
        endpoints = (('/pair', self.mobile_pair, ['POST'], 'Reality Companion mobile pair'),
                     ('/status', self.mobile_status, ['GET'], 'Reality Companion mobile status'),
                     ('/snapshot', self.mobile_snapshot, ['GET'], 'Read the paired user snapshot through V2 native authorization'),
                     ('/room/create', self.mobile_create_room, ['POST'], 'Reality Companion mobile room'),
                     ('/room/invite', self.mobile_invite_room, ['POST'], 'Reality Companion mobile room invitation'),
                     ('/room/join', self.mobile_join_room, ['POST'], 'Reality Companion mobile room join'),
                     ('/game/room/create', self.mobile_create_game_room, ['POST'], 'Reality Companion mobile game room'),
                     ('/room/prepare', self.mobile_prepare_rooms, ['POST'], 'Reality Companion warm mobile room'),
                     ('/location', self.mobile_location, ['POST'], 'Reality Companion mobile location'),
                     ('/location/heartbeat', self.mobile_location_heartbeat, ['POST'], 'Reality Companion mobile location heartbeat'),
                     ('/location/revoke', self.mobile_revoke_location, ['POST'], 'Reality Companion revoke mobile location'),
                     ('/device/status', self.mobile_device_status, ['POST'], 'Reality Companion mobile device status'),
                     ('/device/activity', self.mobile_device_activity, ['POST'], 'Reality Companion mobile activity summary'),
                     ('/telemetry', self.mobile_telemetry, ['POST'], 'Reality Companion mobile telemetry'),
                     ('/screen/heartbeat', self.mobile_screen_heartbeat, ['POST'], 'Reality Companion mobile screen status'),
                     ('/session/close', self.mobile_close_session, ['POST'], 'Reality Companion mobile close'))
        for prefix in prefixes:
            for path, handler, methods, description in endpoints:
                register_api(f'{prefix}{path}', route(handler), methods, description)
