"""V2-owned reality lifecycle; no Star registration."""

from __future__ import annotations

from .common import *
from ..service import NativeService
from ..usage import TokenUsageTracker
from .settings_methods import RealitySettingsMethods
from .speech_methods import RealitySpeechMethods
from .activity_methods import RealityActivityMethods
from .model_methods import RealityModelMethods

class RealityRuntime(NativeService, RealitySettingsMethods, RealitySpeechMethods, RealityActivityMethods, RealityModelMethods, MobileGatewayMixin, WakeupAlarmMixin):

    def __init__(self, context: Context, config: AstrBotConfig, data_root, controller) -> None:
        self.context = context
        self.controller = controller
        self.config = config
        self.mihome_config = self.config.get('mihome') if isinstance(self.config.get('mihome'), dict) else {}
        if isinstance(self.config, dict):
            self.config['mihome'] = self.mihome_config
        self.mihome_data_manager = None
        self.mihome_client = None
        self.mihome_web_api = None
        self.mihome_extension_api = RealityMiHomeExtensionAPI(self)
        pass
        self.extension_api = RealityExtensionAPI(self)
        self.data_dir = Path(data_root)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.token_usage = TokenUsageTracker(self.data_dir / 'reality_companion_token_usage.json',
            source='reality', display_name='现实观察与提醒', note='记录摄像头与现实提醒文本模型的实际回执；缺失用量保持未知，不计入普通核心回复或 V2 角色预算。')
        self.data_file = self.data_dir / 'reality_companion.json'
        self.data = self._load_data()
        self._data_lock = asyncio.Lock()
        self._save_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._wakeup_contact_tasks: dict[str, asyncio.Task] = {}
        self._reality_touch_camera_operation_lock = asyncio.Lock()
        self._reality_touch_camera_continuations: dict[str, dict[str, Any]] = {}
        self._mobile_gateway_init()
        self._legacy_migration_attempted = False
        self._alarm_task = None
        self._sync_runtime_config()
        self.plugin_vision_provider_id = self._cfg_str('vision_provider_id', '')
        self.environment_perception_timezone = self._cfg_str('timezone', 'Asia/Shanghai') or 'Asia/Shanghai'
        self.check_interval_seconds = 30
        self.authorized_user_ids = {_single_line(item, 120) for item in self._cfg('authorized_user_ids', []) if _single_line(item, 120)}
        pass
        self._native_provider = None
        self._init_embedded_mihome()

    async def terminate(self) -> None:
        await self._set_official_reminders_paused(True)
        native = self._native_provider
        self._native_provider = None
        if native is not None:
            await native.close()
        try:
            await self._stop_mobile_server()
        except Exception as exc:
            logger.warning('[RealityCompanion] 停止移动端网关失败: %s', _single_line(exc, 180))
        tasks = set(self._background_tasks) | set(self._wakeup_contact_task_registry().values())
        for task in tasks:
            if isinstance(task, asyncio.Task) and (not task.done()):
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._wakeup_contact_tasks.clear()
        self._alarm_task = None
        if self.mihome_web_api is not None:
            try:
                await self.mihome_web_api.terminate()
            except Exception as exc:
                logger.warning('[RealityCompanion] 停止内置米家 Web API 失败: %s', _single_line(exc, 160))
        if self.mihome_client is not None:
            try:
                await self.mihome_client.terminate()
            except Exception as exc:
                logger.warning('[RealityCompanion] 停止内置米家客户端失败: %s', _single_line(exc, 160))
        await asyncio.to_thread(self._save_data_sync)
        await asyncio.to_thread(self.token_usage.save, force=True)

    def build_native(self, version):
        from companion_v2.reality.provider import NativeRealityProvider
        self._native_provider = NativeRealityProvider(self, self.data_dir, version=version)
        return self._native_provider

    async def camera_snapshot(self, user_id, purpose, *, source='manual'):
        """Use the same local camera policy and device path as the internal API."""
        return await self.extension_api.camera_snapshot(user_id, purpose, source=source)

    async def initialize(self):
        if self._cfg_bool('enabled', True):
            for user in self.data.get('users', {}).values():
                if not isinstance(user, dict):
                    continue
                for reminder in self._reality_touch_reminder_registry(user).values():
                    if isinstance(reminder, dict) and reminder.get('status') == 'delivering':
                        reminder.update(status='delivery_unknown', delivery_error='上次交付已中断，保持未知且不自动重放')
            started = await self._start_mobile_server()
            if self._mobile_enabled() and not started:
                raise RuntimeError('移动端网关启动失败，请检查配对令牌和端口配置')
            await self._set_official_reminders_paused(False)
            self._alarm_task = self._create_lifecycle_background_task(self._alarm_loop(), label='alarms')
            self._create_lifecycle_background_task(self._care_loop(), label='care')
            # 旧版运行记录（音响输出、摄像头设备目录、按用户的摄像头授权）只补一次；
            # 关掉这个开关时不再重复尝试。
            if not self.data.get('legacy_migration_completed'):
                self._create_lifecycle_background_task(self._migration_loop(), label='legacy-migration')

    async def _care_loop(self) -> None:
        """Observe devices for the care layer; phase 1 records impulses, never acts."""
        while True:
            try:
                if self.mihome_web_api is not None:
                    await self.mihome_web_api.extension_care_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('[RealityCompanion] 在意轮询失败: %s', _single_line(exc, 160))
            await asyncio.sleep(300)

    async def _set_official_reminders_paused(self, paused):
        manager = self._official_cron_manager()
        if manager is None or not callable(getattr(manager, 'update_job', None)):
            return
        changed = False
        for user in self.data.get('users', {}).values():
            if not isinstance(user, dict):
                continue
            for reminder in self._reality_touch_reminder_registry(user).values():
                if not isinstance(reminder, dict) or reminder.get('status') != 'scheduled' or not reminder.get('job_id'):
                    continue
                if not paused and not reminder.get('paused_by_v2'):
                    continue
                if not paused and _safe_int(reminder.get('scheduled_ts'), 0, 0) <= _now_ts():
                    reminder.update(status='expired', paused_by_v2=False)
                    changed = True
                    continue
                try:
                    await manager.update_job(reminder['job_id'], enabled=not paused)
                except Exception as error:
                    logger.warning('[CompanionV2] 现实提醒启停未确认 error_type=%s', type(error).__name__)
                    continue
                reminder['paused_by_v2'] = bool(paused)
                changed = True
        if changed:
            await asyncio.to_thread(self._save_data_sync)
