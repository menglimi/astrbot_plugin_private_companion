"""V2 reality settings services."""
from __future__ import annotations
from companion_v2.execution.models import TaskError
from .common import *

class RealitySettingsMethods:

    def _cfg(self, dotted_key: str, default: Any=None) -> Any:
        if dotted_key in self.config:
            return self.config.get(dotted_key, default)
        current: Any = self.config
        for part in dotted_key.split('.'):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current.get(part)
        return default if current is None else current

    def _cfg_str(self, key: str, default: str='') -> str:
        return str(self._cfg(key, default) or '').strip()

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return self._coerce_config_bool(self._cfg(key, default), default)

    @staticmethod
    def _coerce_config_bool(value: Any, default: bool=False) -> bool:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '启用', '开启', '开', '是'}:
                return True
            if lowered in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '停用', '关闭', '关', '否', ''}:
                return False
        return default if value is None else bool(value)

    def _cfg_int(self, key: str, default: int, minimum: int=0, maximum: int | None=None) -> int:
        return _safe_int(self._cfg(key, default), default, minimum, maximum)

    def _sync_runtime_config(self) -> None:
        """Refresh device runtime fields after config mutation or startup."""
        self.enable_experimental_bluetooth_wakeup = self._cfg_bool('enabled', False)
        self.enable_reality_touch_camera = self._cfg_bool('camera.enabled', False)
        self.reality_touch_camera_index = self._cfg_int('camera.index', 0, 0, 100000)
        self.reality_touch_camera_min_interval_seconds = self._cfg_int('camera.min_interval_seconds', 60, 10, 3600)
        self.reality_touch_camera_capture_timeout_seconds = self._cfg_int('camera.capture_timeout_seconds', 5, 2, 20)
        self.reality_touch_camera_analysis_timeout_seconds = self._cfg_int('camera.analysis_timeout_seconds', 25, 5, 90)
        self.enable_reality_touch_camera_proactive_curiosity = self._cfg_bool('camera.proactive_curiosity_enabled', False)
        self.reality_touch_camera_proactive_min_tier = self._cfg_int('camera.proactive_min_tier', 4, 1, 5)
        self.reality_touch_camera_proactive_max_daily = self._cfg_int('camera.proactive_max_daily', 1, 0, 10)
        self.reality_touch_camera_proactive_cooldown_minutes = self._cfg_int('camera.proactive_cooldown_minutes', 240, 10, 1440)
        self.tts_local_playback_volume = self._cfg_int('audio.default_playback_volume', 35, 0, 100)
        self.home_enabled = self._cfg_bool('home.enabled', False)
        self.home_provider = self._cfg_str('home.provider', 'home_assistant') or 'home_assistant'
        self.home_base_url = self._cfg_str('home.base_url', '').rstrip('/')
        self.home_default_area = self._cfg_str('home.default_area', '')
        self.home_request_timeout_seconds = self._cfg_int('home.request_timeout_seconds', 5, 1, 30)
        self.health_enabled = self._cfg_bool('health.enabled', False)
        self.health_ttl_seconds = self._cfg_int('health.ttl_seconds', 3600, 60, 604800)

    def _normalize_group_config_value(self, group_key: str, key: str, value: Any) -> Any:
        defaults = _CAMERA_CONFIG_DEFAULTS if group_key == 'camera' else _AUDIO_CONFIG_DEFAULTS
        default = defaults[key]
        if isinstance(default, bool):
            return self._coerce_config_bool(value, default)
        if group_key == 'camera':
            minimum, maximum = _CAMERA_CONFIG_BOUNDS[key]
        else:
            minimum, maximum = (0, 100)
        return _safe_int(value, int(default), minimum, maximum)

    def _group_config_field_values(self, group_key: str, key: str) -> list[Any]:
        values: list[Any] = []
        dotted_key = f'{group_key}.{key}'
        if dotted_key in self.config:
            values.append(self.config.get(dotted_key))
        group = self.config.get(group_key)
        if isinstance(group, dict) and key in group:
            values.append(group.get(key))
        return values

    def _group_config_is_default(self, group_key: str, defaults: dict[str, Any]) -> bool:
        for key, default in defaults.items():
            for value in self._group_config_field_values(group_key, key):
                if self._normalize_group_config_value(group_key, key, value) != default:
                    return False
        return True

    def _set_group_config_field(self, group_key: str, key: str, value: Any) -> bool:
        changed = False
        dotted_key = f'{group_key}.{key}'
        dotted_present = dotted_key in self.config
        if dotted_present and self.config.get(dotted_key) != value:
            self.config[dotted_key] = value
            changed = True
        group = self.config.get(group_key)
        if isinstance(group, dict):
            if group.get(key, _MISSING) != value:
                group[key] = value
                changed = True
        elif not dotted_present:
            self.config[group_key] = {key: value}
            changed = True
        return changed

    def _merge_legacy_config_group(self, group_key: str, defaults: dict[str, Any], legacy_keys: dict[str, str], legacy_config: dict[str, Any]) -> bool:
        whole_group_is_default = self._group_config_is_default(group_key, defaults)
        changed = False
        for target_key, legacy_key in legacy_keys.items():
            if legacy_key not in legacy_config:
                continue
            field_exists = bool(self._group_config_field_values(group_key, target_key))
            if not whole_group_is_default and field_exists:
                continue
            value = self._normalize_group_config_value(group_key, target_key, legacy_config.get(legacy_key))
            changed = self._set_group_config_field(group_key, target_key, value) or changed
        return changed

    def _migrate_legacy_config(self, legacy_config: dict[str, Any]) -> bool:
        """Import defaults only when the new config has no explicit choice."""
        if not isinstance(self.config, dict) or not isinstance(legacy_config, dict):
            return False
        changed = False
        if 'enabled' in legacy_config:
            current = self.config.get('enabled', _MISSING)
            current_is_default = current is _MISSING or not self._coerce_config_bool(current, False)
            if current_is_default:
                migrated_enabled = self._coerce_config_bool(legacy_config.get('enabled'), False)
                if current is _MISSING or current != migrated_enabled:
                    self.config['enabled'] = migrated_enabled
                    changed = True
        changed = self._merge_legacy_config_group('camera', _CAMERA_CONFIG_DEFAULTS, _LEGACY_CAMERA_CONFIG_KEYS, legacy_config) or changed
        changed = self._merge_legacy_config_group('audio', _AUDIO_CONFIG_DEFAULTS, {'default_playback_volume': 'audio_default_playback_volume'}, legacy_config) or changed
        return changed

    def _host_identity(self, user_id: Any) -> dict[str, Any]:
        normalized = _single_line(user_id, 120)
        try:
            resolved = self.v2_host_api().mobile_target_context(normalized)
            if resolved is not None:
                from companion_v2.identity_policy.object_access import ObjectAccessPolicy
                _, application, context = resolved
                admitted = ObjectAccessPolicy(application.runtime).feature_access(
                    context.actor, scope=context.scope, action='memory.manage',
                    observed_at=self.controller.now())
                identity = self.v2_host_api().reality_mobile_context(normalized) or {}
                return {**identity, 'user_id': normalized, 'is_admin': admitted.is_success,
                        'reality_subject_ref': context.key}
        except Exception:
            pass
        return {}

    def _permission_identity_id(self, user_id: Any) -> str:
        return _single_line(user_id, 120)

    def _is_configured_admin_user_id(self, user_id: Any) -> bool:
        normalized = _single_line(user_id, 120)
        return normalized in self.authorized_user_ids or bool(self._host_identity(normalized).get('is_admin'))

    def _register_page_api(self) -> None:
        pass

    async def page_status(self) -> dict[str, Any]:
        return {'ok': True, 'data': self._reality_touch_page_snapshot(), 'integration': self.integration_status()}

    async def page_action(self) -> dict[str, Any]:
        if request is None:
            return {'ok': False, 'message': 'AstrBot 页面请求上下文不可用'}
        try:
            payload = await request.json(default={}) or {}
        except (LookupError, RuntimeError):
            return {'ok': False, 'message': 'AstrBot 页面请求上下文不可用'}
        return await self._perform_page_action(payload)

    async def _perform_page_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = _single_line(payload.get('action'), 40).lower()
        mihome_page_snapshot: dict[str, Any] | None = None
        try:
            if action == 'mobile_pairing_token':
                rotate = payload.get('rotate', False)
                if type(rotate) is not bool:
                    raise ValueError('令牌轮换参数无效')
                result = await self._mobile_pairing_token_command(rotate=rotate)
            elif action.startswith('mihome_'):
                mihome_api = self._mihome_api()
                handler = getattr(mihome_api, 'page_action', None) if mihome_api is not None else None
                if not callable(handler):
                    raise TaskError('native_mihome_unavailable', self.mihome_unavailable_reason())
                mihome_payload = dict(payload)
                mihome_payload['action'] = action.removeprefix('mihome_')
                page_result = await handler(mihome_payload)
                if not isinstance(page_result, dict) or page_result.get('ok') is not True:
                    raise ValueError(str(page_result.get('message') or '米家操作未完成') if isinstance(page_result, dict) else '米家操作未完成')
                mihome_page_snapshot = page_result.get('data') if isinstance(page_result.get('data'), dict) else {}
                action_result = page_result.get('result') if isinstance(page_result.get('result'), dict) else {}
                mihome_page_snapshot['mihome_action_result'] = action_result
                result = action_result
            elif action in {'scan_camera', 'scan_cameras'}:
                result = self._reality_touch_scan_camera_devices()
            elif action == 'scan_audio':
                result = await asyncio.to_thread(self._reality_touch_audio_devices)
            elif action in {'request_audio_consent', 'revoke_audio_consent'}:
                user_id = _single_line(payload.get('user_id'), 120)
                identity = self.v2_host_api().reality_mobile_context(user_id)
                if not identity:
                    raise ValueError('请选择本人已有 V2 私聊窗口的用户')
                user = self._user(user_id)
                user['umo'] = f"{self.v2_host_api().mobile_target_context(user_id)[2].scope.platform}:FriendMessage:{user_id}"
                if action == 'request_audio_consent':
                    prompt, _ = self._wakeup_alarm_command(user, '授权')
                    result = {'user_id': user_id, 'confirmation_required': True, 'prompt': prompt}
                else:
                    prompt, _ = self._wakeup_alarm_command(user, '撤销音频授权')
                    result = {'user_id': user_id, 'confirmation_required': False, 'prompt': prompt, 'confirmed': False}
            elif action in {'save_global_config', 'save_config'}:
                result = await self._reality_touch_save_global_config(payload)
            elif action in {'home_request', 'external_reality_request', 'reality_touch_request'}:
                user_id = _single_line(payload.get('user_id'), 120)
                request_text = _single_line(payload.get('request'), 500)
                if not user_id or not request_text:
                    raise ValueError('请选择用户并填写现实触及请求')
                if any((token in request_text for token in ('健康', '身体', '心率', '步数', '睡眠', '血氧', '体温'))):
                    context = self.mobile_context(user_id)
                    telemetry = context.get('telemetry') if isinstance(context, dict) else {}
                    telemetry = telemetry if isinstance(telemetry, dict) else {}
                    reason = _single_line(telemetry.get('reason'), 64)
                    result = {'ok': True, 'handled': True, 'operation': 'get_health_summary', 'available': bool(telemetry.get('available')), 'summary': _single_line(telemetry.get('summary') or {'telemetry_disabled': '健康数据接收开关未开启', 'no_recent_telemetry': '手机尚未上报健康数据', 'telemetry_expired': '最近健康数据已过期'}.get(reason, '暂无可用健康数据'), 300), 'telemetry': telemetry}
                else:
                    result = await self._home_request(request_text)
                if not isinstance(result, dict) or result.get('ok') is not True:
                    reason_labels = {'user_not_authorized': '该用户尚未完成现实触及授权', 'home_disabled': '家居模块未启用', 'home_not_configured': '请先在家居模块填写服务地址和访问令牌', 'home_request_failed': '家居服务请求失败'}
                    reason = _single_line(result.get('reason'), 80) if isinstance(result, dict) else ''
                    return {'ok': False, 'message': reason_labels.get(reason, '现实触及请求暂时无法处理'), 'result': result if isinstance(result, dict) else {}}
            elif action in {'select_audio', 'select_output'}:
                result = self._reality_touch_select_audio_device(_single_line(payload.get('device_id'), 96), payload.get('playback_volume'))
                self._save_data_sync()
            elif action == 'save_camera_config':
                self.enable_reality_touch_camera = bool(payload.get('camera_enabled'))
                self.reality_touch_camera_index = _safe_int(payload.get('camera_index'), 0, 0, 100000)
                self.reality_touch_camera_min_interval_seconds = _safe_int(payload.get('min_interval_seconds'), 60, 10, 3600)
                self.reality_touch_camera_capture_timeout_seconds = _safe_int(payload.get('capture_timeout_seconds'), 5, 2, 20)
                self.reality_touch_camera_analysis_timeout_seconds = _safe_int(payload.get('analysis_timeout_seconds'), 25, 5, 90)
                self.enable_reality_touch_camera_proactive_curiosity = bool(payload.get('proactive_curiosity_enabled'))
                self.reality_touch_camera_proactive_min_tier = _safe_int(payload.get('proactive_min_tier'), 4, 1, 5)
                self.reality_touch_camera_proactive_max_daily = _safe_int(payload.get('proactive_max_daily'), 1, 0, 10)
                self.reality_touch_camera_proactive_cooldown_minutes = _safe_int(payload.get('proactive_cooldown_minutes'), 240, 10, 1440)
                result = {'saved': self._save_runtime_camera_config()}
            elif action in {'test_audio', 'test'} and (action == 'test_audio' or _single_line(payload.get('test_kind'), 24).lower() == 'device'):
                result = {'played': await self._play_reality_touch_test_audio(payload.get('playback_volume'))}
            elif action == 'test_camera':
                user_id = _single_line(payload.get('user_id'), 120)
                result = await self._reality_touch_camera_snapshot_for_user(user_id, _single_line(payload.get('purpose'), 120) or '管理员从现实触及页面测试摄像头单帧', include_preview=True, source='page_test')
            elif action in {'update_audio_policy', 'save_policy'}:
                user = self._user(payload.get('user_id'), create=False)
                if not user:
                    raise ValueError('没有找到该用户')
                result = self._reality_touch_update_policy(user, payload)
                self._save_data_sync()
            elif action in {'update_camera_policy', 'save_camera_policy'}:
                user = self._user(payload.get('user_id'), create=False)
                if not user:
                    raise TaskError('camera_user_not_configured', '请先确认该用户的摄像头授权')
                result = self._reality_touch_update_camera_policy(user, payload, user_id=_single_line(payload.get('user_id'), 120))
                self._save_data_sync()
            elif action == 'consent_camera':
                user_id = _single_line(payload.get('user_id'), 120)
                if type(payload.get('confirmed')) is not bool:
                    raise ValueError('摄像头确认需要明确的确认或撤销指令')
                if not self._reality_touch_camera_user_eligible(user_id):
                    raise TaskError('camera_user_ineligible', '摄像头确认需要已授权的 V2 管理者身份')
                user = self._user(user_id, create=True)
                if payload['confirmed']:
                    user['reality_touch_camera_consent'] = {
                        'confirmed': True, 'version': self._REALITY_TOUCH_CAMERA_CONSENT_VERSION,
                        'confirmed_at': int(time.time()), 'confirmation_text': '陪伴小屋明确确认',
                        'granted_capabilities': [self._REALITY_TOUCH_CAMERA_CAPABILITY]}
                    self._reality_touch_camera_policy(user)['enabled'] = True
                else:
                    user.pop('reality_touch_camera_consent', None)
                    self._reality_touch_camera_policy(user)['enabled'] = False
                user.pop('reality_touch_pending_consent', None)
                self._save_data_sync()
                result = {'user_id': user_id, 'confirmed': payload['confirmed']}
            elif action == 'save':
                user = self._user(payload.get('user_id'), create=False)
                if not user:
                    raise ValueError('没有找到该用户')
                result = self._reality_touch_update_alarm(user, payload)
                self._save_data_sync()
            elif action in {'disable', 'stop_session'}:
                user = self._user(payload.get('user_id'), create=False)
                if not user:
                    raise ValueError('没有找到该用户')
                if action == 'disable':
                    self._wakeup_alarm_for_user(user)['enabled'] = False
                self._stop_wakeup_contact_session(user)
                self._save_data_sync()
                result = {'stopped': True}
            elif action == 'cancel_reminder':
                result = {'cancelled': await self._cancel_reality_touch_official_reminder(_single_line(payload.get('user_id'), 120), reminder_id=_single_line(payload.get('reminder_id'), 40))}
            elif action == 'test':
                user = self._user(payload.get('user_id'), create=False)
                if not user:
                    raise ValueError('没有找到该用户')
                alarm = copy.deepcopy(self._wakeup_alarm_for_user(user))
                if 'message' in payload:
                    alarm['message'] = _single_line(payload.get('message'), 240)
                result = {'played': await self._play_wakeup_alarm(copy.deepcopy(user), alarm, test=True, volume=payload.get('playback_volume'))}
            else:
                return {'ok': False, 'message': '不支持的操作'}
        except Exception as exc:
            return {'ok': False, 'message': _single_line(exc, 240),
                    'code': exc.code if isinstance(exc, TaskError) else 'native_reality_action_failed'}
        snapshot = self._reality_touch_page_snapshot()
        if mihome_page_snapshot is not None:
            snapshot['mihome'] = mihome_page_snapshot
        if action == 'test_camera' and isinstance(result, dict):
            preview = str(result.get('preview_data_url') or '')
            if preview.startswith('data:image/jpeg;base64,'):
                snapshot['camera_preview'] = {'user_id': _single_line(payload.get('user_id'), 120), 'captured_at': int(time.time()), 'data_url': preview, 'ephemeral': True}
        return {'ok': True, 'result': result, 'data': snapshot}

    def _reality_touch_configuration_snapshot(self) -> dict[str, Any]:
        """Return editable settings without exposing the mobile pairing secret."""
        return {'enabled': bool(self.enable_experimental_bluetooth_wakeup), 'vision_provider_id': _single_line(self.plugin_vision_provider_id, 160), 'timezone': _single_line(self.environment_perception_timezone, 80) or 'Asia/Shanghai', 'authorized_user_ids': sorted(self.authorized_user_ids), 'audio_default_playback_volume': _safe_int(self.tts_local_playback_volume, 35, 0, 100), 'home': {'enabled': bool(self.home_enabled), 'provider': _single_line(self.home_provider, 48) or 'home_assistant', 'base_url': _single_line(self.home_base_url, 240), 'access_token_configured': bool(self._cfg_str('home.access_token', '')), 'default_area': _single_line(self.home_default_area, 80), 'request_timeout_seconds': int(self.home_request_timeout_seconds), 'available': bool(self.home_enabled and self.home_base_url and self._cfg_str('home.access_token', ''))}, 'health': {'enabled': bool(self.health_enabled), 'ttl_seconds': int(self.health_ttl_seconds), 'available': bool(self.health_enabled and self._mobile_enabled())}, 'mobile': {'gateway_version': PLUGIN_VERSION, 'api_version': MOBILE_API_VERSION, 'enabled': self._mobile_enabled(), 'host': self._mobile_host(), 'port': self._mobile_port(), 'public_base_url': _single_line(self._cfg_str('mobile.public_base_url', ''), 300).rstrip('/'), 'allowed_user_id': self._mobile_allowed_user_id(), 'core_user_id': self._mobile_core_user_id(), 'session_ttl_hours': self._cfg_int('mobile.session_ttl_hours', 168, 1, 720), 'location_ttl_seconds': self._mobile_location_ttl(), 'amap_reverse_geocode_enabled': self._mobile_amap_enabled(), 'amap_api_key_configured': bool(self._mobile_amap_api_key()), 'amap_cache_ttl_seconds': self._mobile_amap_cache_ttl(), 'amap_request_timeout_seconds': self._mobile_amap_timeout_seconds(), 'telemetry_enabled': self._mobile_telemetry_enabled(), 'telemetry_ttl_seconds': self._mobile_telemetry_ttl(), 'activity_enabled': self._mobile_activity_enabled(), 'activity_ttl_seconds': self._mobile_activity_ttl(), 'proxy_rooms': self._cfg_bool('mobile.proxy_rooms', True), 'screen_upload_enabled': self._mobile_screen_upload_enabled(), 'pairing_token_configured': bool(self._mobile_pairing_token()), 'running': self._mobile_server_runner is not None, 'bound_port': _safe_int(self._mobile_server_bound_port, 0, 0, 65535), 'active_sessions': len(self._mobile_sessions)}}

    async def _reality_touch_save_global_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        enabled = self._coerce_config_bool(payload.get('enabled'), False)
        vision_provider_id = _single_line(payload.get('vision_provider_id'), 160)
        timezone = _single_line(payload.get('timezone'), 80) or 'Asia/Shanghai'
        raw_ids = payload.get('authorized_user_ids', [])
        if isinstance(raw_ids, str):
            raw_ids = re.split('[,\\n，；;]+', raw_ids)
        authorized_user_ids = sorted({_single_line(item, 120) for item in (raw_ids if isinstance(raw_ids, (list, tuple, set)) else []) if _single_line(item, 120)})
        self.config['enabled'] = enabled
        self.config['vision_provider_id'] = vision_provider_id
        self.config['timezone'] = timezone
        self.config['authorized_user_ids'] = authorized_user_ids
        self._set_group_config_field('audio', 'default_playback_volume', _safe_int(payload.get('audio_default_playback_volume'), 35, 0, 100))
        mobile = payload.get('mobile') if isinstance(payload.get('mobile'), dict) else {}
        home = payload.get('home') if isinstance(payload.get('home'), dict) else {}
        health = payload.get('health') if isinstance(payload.get('health'), dict) else {}
        for key, value in {'enabled': self._coerce_config_bool(home.get('enabled'), False), 'provider': _single_line(home.get('provider'), 48) or 'home_assistant', 'base_url': _single_line(home.get('base_url'), 240).rstrip('/'), 'default_area': _single_line(home.get('default_area'), 80), 'request_timeout_seconds': _safe_int(home.get('request_timeout_seconds'), 5, 1, 30)}.items():
            self._set_group_config_field('home', key, value)
        home_token = _single_line(home.get('access_token'), 400)
        if home_token:
            self._set_group_config_field('home', 'access_token', home_token)
        for key, value in {'enabled': self._coerce_config_bool(health.get('enabled'), False), 'ttl_seconds': _safe_int(health.get('ttl_seconds'), 3600, 60, 604800)}.items():
            self._set_group_config_field('health', key, value)
        mobile_values = {'enabled': self._coerce_config_bool(mobile.get('enabled'), False), 'host': _single_line(mobile.get('host'), 120) or '0.0.0.0', 'port': _safe_int(mobile.get('port'), 6322, 1, 65535), 'public_base_url': _single_line(mobile.get('public_base_url'), 300).rstrip('/'), 'allowed_user_id': _single_line(mobile.get('allowed_user_id'), 120), 'session_ttl_hours': _safe_int(mobile.get('session_ttl_hours'), 168, 1, 720), 'location_ttl_seconds': _safe_int(mobile.get('location_ttl_seconds'), 900, 60, 86400), 'amap_reverse_geocode_enabled': self._coerce_config_bool(mobile.get('amap_reverse_geocode_enabled'), False), 'amap_cache_ttl_seconds': _safe_int(mobile.get('amap_cache_ttl_seconds'), 1800, 60, 604800), 'amap_request_timeout_seconds': _safe_int(mobile.get('amap_request_timeout_seconds'), 5, 1, 20), 'telemetry_enabled': self._coerce_config_bool(mobile.get('telemetry_enabled'), False), 'telemetry_ttl_seconds': _safe_int(mobile.get('telemetry_ttl_seconds'), 3600, 60, 604800), 'activity_enabled': self._coerce_config_bool(mobile.get('activity_enabled'), False), 'activity_ttl_seconds': _safe_int(mobile.get('activity_ttl_seconds'), 900, 60, 86400), 'proxy_rooms': self._coerce_config_bool(mobile.get('proxy_rooms'), True), 'screen_upload_enabled': self._coerce_config_bool(mobile.get('screen_upload_enabled'), True)}
        for key, value in mobile_values.items():
            self._set_group_config_field('mobile', key, value)
        pairing_token = _single_line(mobile.get('pairing_token'), 240)
        if pairing_token:
            self._set_group_config_field('mobile', 'pairing_token', pairing_token)
        amap_api_key = _single_line(mobile.get('amap_api_key'), 240)
        if amap_api_key:
            self._set_group_config_field('mobile', 'amap_api_key', amap_api_key)
        saver = getattr(self.config, 'save_config', None)
        if callable(saver):
            saver()
        self._sync_runtime_config()
        self.plugin_vision_provider_id = vision_provider_id
        self.environment_perception_timezone = timezone
        self.authorized_user_ids = set(authorized_user_ids)
        await self._stop_mobile_server()
        await self._start_mobile_server()
        return {'saved': True, 'mobile_running': self._mobile_server_runner is not None}

    def _save_runtime_camera_config(self) -> bool:
        camera = self.config.get('camera') if isinstance(self.config, dict) else None
        if not isinstance(camera, dict):
            camera = {}
            self.config['camera'] = camera
        camera.update({'enabled': self.enable_reality_touch_camera, 'index': self.reality_touch_camera_index, 'min_interval_seconds': self.reality_touch_camera_min_interval_seconds, 'capture_timeout_seconds': self.reality_touch_camera_capture_timeout_seconds, 'analysis_timeout_seconds': self.reality_touch_camera_analysis_timeout_seconds, 'proactive_curiosity_enabled': self.enable_reality_touch_camera_proactive_curiosity, 'proactive_min_tier': self.reality_touch_camera_proactive_min_tier, 'proactive_max_daily': self.reality_touch_camera_proactive_max_daily, 'proactive_cooldown_minutes': self.reality_touch_camera_proactive_cooldown_minutes})
        saver = getattr(self.config, 'save_config', None)
        try:
            if callable(saver):
                saver()
            return True
        except Exception as exc:
            logger.warning('[RealityCompanion] 保存摄像头配置失败: %s', _single_line(exc, 160))
            return False

    def _apply_legacy_migrated_settings(self, changes: dict[str, Any], expected_state_token: str,
                                        state_fields=None) -> dict[str, Any]:
        """Save only the portable V1 Reality settings approved by the converter."""
        from companion_v2.legacy.reality_migration import (
            REALITY_INTEGER_BOUNDS,
            REALITY_SETTING_DEFAULTS,
            reality_settings_state_token,
        )

        if not isinstance(self.config, dict) or not isinstance(changes, dict) or not changes:
            return {'saved': False, 'reason': 'reality_config_unavailable'}
        fields = tuple(sorted(set(state_fields or changes)))
        if (set(changes) - set(REALITY_SETTING_DEFAULTS)
                or not fields or set(fields) - set(REALITY_SETTING_DEFAULTS)
                or set(changes) - set(fields)):
            return {'saved': False, 'reason': 'reality_migration_field_not_allowed'}
        if reality_settings_state_token(self.config, fields) != expected_state_token:
            return {'saved': False, 'reason': 'reality_config_changed'}

        for field, value in changes.items():
            if field == 'camera.enabled' and type(value) is not bool:
                return {'saved': False, 'reason': 'reality_camera_setting_invalid'}
            if field in REALITY_INTEGER_BOUNDS:
                minimum, maximum = REALITY_INTEGER_BOUNDS[field]
                if type(value) is not int or not minimum <= value <= maximum:
                    return {'saved': False, 'reason': 'reality_numeric_setting_invalid'}
            if field == 'timezone' and (not isinstance(value, str) or not value.strip() or len(value) > 80):
                return {'saved': False, 'reason': 'reality_timezone_invalid'}
            if field == 'vision_provider_id' and (type(value) is not str or not value.strip() or len(value) > 160):
                return {'saved': False, 'reason': 'reality_vision_provider_invalid'}

        from copy import deepcopy
        previous = deepcopy(dict(self.config))

        try:
            for field, value in changes.items():
                group, separator, key = field.partition('.')
                if separator:
                    self._set_group_config_field(group, key, value)
                else:
                    self.config[field] = value
            saver = getattr(self.config, 'save_config', None)
            if not callable(saver):
                raise RuntimeError('reality_config_save_unavailable')
            if saver() is False:
                raise RuntimeError('reality_config_save_failed')
        except Exception:
            self.config.clear()
            self.config.update(previous)
            raise

        self._sync_runtime_config()
        self.environment_perception_timezone = self._cfg_str('timezone', 'Asia/Shanghai') or 'Asia/Shanghai'
        if 'vision_provider_id' in changes:
            self.plugin_vision_provider_id = self._cfg_str('vision_provider_id', '')
        return {'saved': True, 'fields': sorted(changes)}
