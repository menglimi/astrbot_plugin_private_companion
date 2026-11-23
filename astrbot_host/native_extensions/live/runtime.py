"""V2-owned live lifecycle; no Star registration."""

from __future__ import annotations

from .common import *
from ..service import NativeService
from ..usage import TokenUsageTracker, flush_model_usage
from .settings_methods import LiveSettingsMethods
from .rooms_methods import LiveRoomsMethods
from .media_methods import LiveMediaMethods
from .speech_methods import LiveSpeechMethods
from .activity_methods import LiveActivityMethods
from .model_methods import LiveModelMethods
from .state import LiveStateStore

class LiveRuntime(NativeService, LiveSettingsMethods, LiveRoomsMethods, LiveMediaMethods, LiveSpeechMethods, LiveActivityMethods, LiveModelMethods, SubtitleMixin, MouthSyncMixin, Live2DMixin, SoullinkMixin, TwitchMixin):

    def __init__(self, context: Context, config: AstrBotConfig, data_root, controller):
        self.context = context
        self.controller = controller
        self.data_dir = Path(data_root)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.token_usage = TokenUsageTracker(self.data_dir / 'live_token_usage.json',
                                             source='live', display_name='直播与表演')
        self._model_usage_tasks = set()
        self._live_state_store = LiveStateStore(self)
        self.extension_api = LiveNativeExtensionAPI(self)
        self._native_provider = None
        self.config = config or {}
        self._live_config_migrations = migrate_live_config_keys(self.config)
        native_v2 = self.config.get('native_v2')
        native_v2 = native_v2 if isinstance(native_v2, dict) else {}
        self.native_v2_enabled = native_v2.get('enabled') is True
        self.native_v2_bili_events_enabled = native_v2.get('bili_events_enabled', True) is True
        self.native_v2_twitch_events_enabled = native_v2.get('twitch_events_enabled', True) is True
        self._config_compatibility_migrations = _migrate_l2d_template_keys(self.config)
        self._auto_discover: bool = self.config.get('auto_discover', True)
        self._manual_host: Optional[str] = self.config.get('vts_host') or None
        port_val = self.config.get('vts_port')
        self._manual_port: Optional[int] = self._safe_parse_port(port_val)
        self._auto_connect: bool = self.config.get('auto_connect', True)
        self._debug_mode: bool = self.config.get('debug_mode', False)
        self._bili_debug_mode: bool = bool(self.config.get('bili_live_debug_log', False))
        self._l2d_tasks: set[asyncio.Task] = set()
        self._mouth_sync_tasks: set[asyncio.Task] = set()
        self._soullink_tasks: set[asyncio.Task] = set()
        self._external_mouth_sync_tasks: dict[str, set[asyncio.Task]] = {}
        self._background_tasks: set[asyncio.Task] = set()
        self._bili_live_client: Optional[BilibiliBlivedmClient | BilibiliLaplaceClient | BilibiliLiveClient | BilibiliOpenLiveClient] = None
        self._bili_live_task: Optional[asyncio.Task] = None
        cache_size = max(10, min(5000, self._safe_parse_int(self.config.get('bili_live_cache_size'), 80)))
        self._bili_events: deque[LiveDanmakuEvent] = deque(maxlen=cache_size)
        self._bili_session_events: deque[LiveDanmakuEvent] = deque(maxlen=500)
        self._bili_pending_reply_events: deque[LiveDanmakuEvent] = deque(maxlen=50)
        self._bili_auto_reply_task: Optional[asyncio.Task] = None
        self._bili_last_auto_reply_at = 0.0
        self._bili_auto_reply_minute_marks: deque[float] = deque(maxlen=120)
        self._bili_auto_reply_history: deque[dict[str, Any]] = deque(maxlen=30)
        self._bili_acknowledged_support_event_ids: set[str] = set()
        self._bili_processing_support_event_ids: set[str] = set()
        self._bilibili_ai_memory_tasks: set[asyncio.Task] = set()
        self._bilibili_ai_written_event_ids: set[str] = set()
        self._bili_session_started_at = 0.0
        self._bili_session_id = ''
        self._bili_summary_written_for_session = False
        self._init_twitch_runtime(cache_size)
        self._private_companion_writeback_seen: set[str] = set()
        self._private_companion_last_state_at = 0.0
        self._bili_reply_event_template: Optional[AstrMessageEvent] = None
        self._bili_area_by_id: dict[int, BilibiliLiveArea] = {}
        self._bili_area_by_key: dict[str, BilibiliLiveArea] = {}
        self._bili_area_loaded_at = 0.0
        self._bili_area_load_task: Optional[asyncio.Task] = None
        self._startup_task: Optional[asyncio.Task] = None
        self._subtitle_server = None
        self._warned_bili_blivedm_fallback = False
        self.page_api = None
        pass
        self.vts = VTSClient(host=self._manual_host or DEFAULT_HOST, port=self._manual_port or DEFAULT_PORT, plugin_name='AstrBot Live Stream Companion', plugin_developer='menglimi')
        self._parameter_vts = VTSRealtimeClient(host=self._manual_host or DEFAULT_HOST, port=self._manual_port or DEFAULT_PORT, plugin_name='AstrBot Live Stream Companion', plugin_developer='menglimi')
        self._vts_parameter_scheduler = VTSParameterScheduler(self._parameter_vts, self._check_parameter_connection, fps=self._safe_parse_int(self.config.get('soullink_fps') if self.config.get('soullink_enabled', False) else self.config.get('mouth_sync_fps'), 20))
        self._soullink_runtime = SoullinkRuntimeBridge(fps=self._safe_parse_int(self.config.get('soullink_fps'), 20), node_path=str(self.config.get('soullink_node_path') or ''), on_frame=self._on_soullink_frame)
        self._soullink_last_vts_parameters: list[dict[str, Any]] = []
        self._connected = False
        self._last_vts_auth_error = ''
        self._vts_auth_lock = asyncio.Lock()

    async def _cancel_external_mouth_sync_tasks(self) -> None:
        tasks: list[asyncio.Task] = []
        for group in list(self._external_mouth_sync_tasks.values()):
            if isinstance(group, set):
                tasks.extend(task for task in list(group) if isinstance(task, asyncio.Task))
        self._external_mouth_sync_tasks.clear()
        pending = [task for task in tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def terminate(self):
        """插件卸载/停用时：断开 VTS 连接，清理资源"""
        async def _step(name: str, action) -> None:
            try:
                await action()
            except Exception as exc:
                logger.warning('[VTS] 卸载清理步骤失败 step=%s: %s', name, exc)

        provider = self._native_provider
        self._native_provider = None
        if provider is not None:
            await _step('native_provider', provider.close)

        steps = (
            ('startup_task', lambda: self._cancel_task_attr('_startup_task')),
            ('l2d_tasks', lambda: self._cancel_task_set(self._l2d_tasks)),
            ('mouth_sync_tasks', lambda: self._cancel_task_set(self._mouth_sync_tasks)),
            ('external_mouth_sync_tasks', self._cancel_external_mouth_sync_tasks),
            ('soullink_tasks', lambda: self._cancel_task_set(self._soullink_tasks)),
            ('bilibili_ai_memory_tasks', lambda: self._cancel_task_set(self._bilibili_ai_memory_tasks)),
            ('background_tasks', lambda: self._cancel_task_set(self._background_tasks)),
            ('bili_auto_reply_task', lambda: self._cancel_task_attr('_bili_auto_reply_task')),
            ('bili_area_load_task', lambda: self._cancel_task_attr('_bili_area_load_task')),
            ('twitch_auto_reply_task', lambda: self._cancel_task_attr('_twitch_auto_reply_task')),
            ('bili_live', self._stop_bili_live),
            ('twitch_live', lambda: self._stop_twitch_live(log=False)),
            ('subtitle_server', self._stop_subtitle_server),
            ('soullink_runtime', self._stop_soullink_runtime),
            ('vts_parameter_scheduler', self._vts_parameter_scheduler.stop),
            ('vts_disconnect', self.vts.disconnect),
            ('parameter_vts_disconnect', self._parameter_vts.disconnect),
            ('flush_model_usage', lambda: flush_model_usage(self)),
        )
        for name, action in steps:
            await _step(name, action)
        logger.info('[VTS] 插件已卸载，VTS 连接已关闭')

    def build_native(self, version):
        from companion_v2.live.provider import NativeLiveProvider
        self._native_provider = NativeLiveProvider(self, version=version)
        return self._native_provider

    async def initialize(self):
        try:
            self._live_bound_reply_session = str(await self.get_kv_data(KV_KEY_BILI_REPLY_SESSION, '') or '').strip()
        except (OSError, TypeError, ValueError):
            self._live_bound_reply_session = ''
        self._live_state_store.refresh_profiles(force=True)
        await self._initialize_runtime()
