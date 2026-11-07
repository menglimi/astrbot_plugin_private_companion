"""The cottage's settings entry for native V2 capability services."""

from __future__ import annotations

import asyncio
import json

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from .perception_api import PerceptionWebAPI

PREFIX = "/astrbot_plugin_private_companion/extensions"


class NativeExtensionWebAPI(PerceptionWebAPI):
    def routes(self):
        return [(PREFIX, self.status, ["GET"], "Read native V2 capability settings"),
                (PREFIX + "/configure", self.configure, ["POST"], "Save and apply native V2 capability settings"),
                (PREFIX + "/action", self.action, ["POST"], "Manage a native V2 device service")]

    @staticmethod
    def _snapshot(registry, name, service):
        data = {"settings": service.config.public(), "schema": service.config.schema,
                "revision": service.config.revision(), "runtime": registry.status(name)}
        if name == "reality":
            data["management"] = service.extension_api.page_snapshot()
        elif name == "live":
            from .native_extensions.live.management import snapshot
            data["management"] = snapshot(service)
        return data

    async def status(self):
        try:
            if request.query:
                raise ValueError("unexpected query fields")
            async with self.controller._control_lock:
                self._authorize()
                registry = getattr(self.controller, "native_extensions", None)
                extensions = {}
                for name in ("reality", "together", "game", "live"):
                    service = registry.service(name) if registry is not None else None
                    if service is not None:
                        extensions[name] = self._snapshot(registry, name, service)
                return self._ok({"extensions": extensions})
        except (TaskError, ValueError) as error:
            return self._error(error)

    async def configure(self):
        try:
            raw = await request.body()
            if request.query or len(raw) > 65536:
                raise ValueError("bounded settings body required")
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("duplicate settings field")
                    values[key] = value
                return values
            data = json.loads(raw, object_pairs_hook=unique)
            if (type(data) is not dict or set(data) != {"extension_id", "updates", "revision"}
                    or data["extension_id"] not in {"reality", "together", "game", "live"}
                    or type(data["updates"]) is not dict or type(data["revision"]) is not str):
                raise ValueError("invalid native settings command")
            async with self.controller._control_lock:
                self._authorize()
                registry = getattr(self.controller, "native_extensions", None)
                if registry is None:
                    raise TaskError("native_settings_unavailable", "V2 native services are not loaded")
                result = await registry.configure_service(data["extension_id"], data["updates"], data["revision"])
                return self._ok(result)
        except (TaskError, ValueError, TypeError) as error:
            return self._error(error)
        except Exception:
            return self._error(TaskError("native_settings_apply_failed", "设置已保存，但功能重启失败，请重新读取运行状态"))

    async def action(self):
        try:
            raw = await request.body()
            if request.query or len(raw) > 65536:
                raise ValueError("bounded action body required")
            data = json.loads(raw)
            if (type(data) is not dict or set(data) != {"extension_id", "action", "payload"}
                    or data['extension_id'] not in {'reality', 'live'}
                    or type(data['action']) is not str or type(data['payload']) is not dict):
                raise ValueError("invalid native management action")
            name = data['extension_id']
            async with self.controller._control_lock:
                self._authorize()
                registry = self.controller.native_extensions
                service = registry.active_service(name) if registry is not None else None
                if service is None:
                    raise TaskError('native_service_not_running', '请先启用此 V2 模块并等待启动完成')

            def current():
                self._authorize()
                if (self.controller.native_extensions is not registry or
                        registry.active_service(name) is not service):
                    raise TaskError('native_service_changed', '模块已停止或重新配置，请刷新状态')

            # Device I/O must not hold the host's conversation control lock.
            if name == 'live':
                from .native_extensions.live.management import action
                result = await action(service, data['action'], data['payload'], current)
            else:
                allowed = {'mihome_start_login', 'mihome_login_status', 'mihome_sync_devices',
                    'mihome_logout', 'mihome_save_mappings', 'mihome_device_status', 'mihome_control_power',
                    'mihome_home_care_status', 'mihome_home_care_save', 'mihome_home_care_run',
                    'mihome_care_status', 'mihome_care_save', 'mihome_care_feedback',
                    'mihome_care_think',
                    'scan_camera', 'test_camera', 'consent_camera', 'update_camera_policy', 'update_audio_policy',
                    'scan_audio', 'select_audio', 'test_audio', 'request_audio_consent',
                    'revoke_audio_consent', 'save', 'disable', 'stop_session', 'test',
                    'cancel_reminder', 'mobile_pairing_token'}
                if data['action'] not in allowed or 'action' in data['payload']:
                    raise ValueError('unsupported native reality action')
                current()
                timeout = 120 if data['action'] == 'test_camera' else 45
                page_result = await asyncio.wait_for(service.extension_api.page_action(
                    {'action': data['action'], **data['payload']}), timeout=timeout)
                if type(page_result) is not dict or not page_result.get('ok'):
                    codes = {'native_mihome_unavailable', 'camera_user_ineligible',
                             'camera_consent_required', 'camera_user_not_configured'}
                    code = page_result.get('code') if isinstance(page_result, dict) else None
                    if code not in codes:
                        code = 'native_reality_action_failed'
                    raise TaskError(code, '操作未完成，请检查现实设备配置与当前授权')
                result = page_result.get('result', {})
                camera_preview = None
                if data['action'] == 'test_camera':
                    page_data = page_result.get('data')
                    candidate = page_data.get('camera_preview') if isinstance(page_data, dict) else None
                    if (isinstance(candidate, dict) and candidate.get('ephemeral') is True
                            and candidate.get('user_id') == data['payload'].get('user_id')
                            and str(candidate.get('data_url') or '').startswith('data:image/jpeg;base64,')):
                        camera_preview = candidate
                    if isinstance(result, dict):
                        result = {key: value for key, value in result.items() if key != 'preview_data_url'}
                if data['action'] in {'test', 'test_audio'} and result.get('played') is not True:
                    raise TaskError('native_reality_playback_failed', '音频未能播放，请核对输出设备、TTS 与当前用户授权')
                if data['action'] == 'cancel_reminder' and result.get('cancelled') is not True:
                    raise TaskError('native_reality_reminder_not_cancelled', '提醒未取消，请刷新提醒状态')
            current()
            response = {**self._snapshot(registry, name, service), 'result': result}
            if name == 'reality' and data['action'] == 'test_camera' and camera_preview is not None:
                response['camera_preview'] = camera_preview
            return self._ok(response)
        except asyncio.TimeoutError:
            return self._error(TaskError('native_management_result_unknown', '结果暂不可确认，请刷新状态后再决定下一步'))
        except (TaskError, ValueError, TypeError) as error:
            return self._error(error)
        except Exception:
            return self._error(TaskError('native_management_failed', '操作未完成，请刷新模块状态'))
