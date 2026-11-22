"""V2-owned together lifecycle; no Star registration."""

from __future__ import annotations

from .common import *
from ..service import NativeService
from .settings_methods import TogetherSettingsMethods
from .rooms_methods import TogetherRoomsMethods
from .media_methods import TogetherMediaMethods
from .speech_methods import TogetherSpeechMethods
from .activity_methods import TogetherActivityMethods
from .model_methods import TogetherModelMethods

class TogetherRuntime(NativeService, TogetherSettingsMethods, TogetherRoomsMethods, TogetherMediaMethods, TogetherSpeechMethods, TogetherActivityMethods, TogetherModelMethods):

    def __init__(self, context: Context, config: AstrBotConfig, data_root, controller) -> None:
        self.context = context
        self.controller = controller
        self.config = config
        self.plugin_root = Path(__file__).resolve().parent
        self.data_dir = Path(data_root)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.data_dir / 'temp'
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.server_enabled = self._cfg_bool('server.enabled', True)
        self.server_host = self._cfg_str('server.host', '127.0.0.1') or '127.0.0.1'
        self.server_port = _clamp_int(self._cfg('server.port', 6321), 6321, 1, 65535)
        self.public_base_url = self._cfg_str('server.public_base_url', '').rstrip('/')
        ticket_minutes = _clamp_int(self._cfg('server.ticket_ttl_minutes', 10), 10, 1, 1440)
        self.ticket_store = RoomTicketStore(ticket_minutes * 60)
        self.primary_user_id = self._cfg_str('conversation.primary_user_id', '')
        self.bot_qq_id = self._cfg_str('conversation.bot_qq_id', '')
        self.persona_id = self._cfg_str('conversation.persona_id', '')
        self.chat_provider_id = self._cfg_str('conversation.chat_provider_id', '')
        self.vision_provider_id = self._cfg_str('conversation.vision_provider_id', '')
        self.history_turns = _clamp_int(self._cfg('conversation.history_turns', 12), 12, 2, 60)
        self.enable_memory_context = self._cfg_bool('conversation.enable_memory_context', True)
        self.call_proactive_enabled = self._cfg_bool('conversation.call_proactive_enabled', True)
        self.model_hangup_enabled = self._cfg_bool('conversation.model_hangup_enabled', True)
        self.call_idle_seconds = _clamp_int(self._cfg('conversation.call_idle_seconds', 120), 120, 60, 900)
        self.sync_astrbot_conversation = self._cfg_bool('conversation.sync_astrbot_conversation', True)
        self.record_visible_turns = self._cfg_bool('conversation.record_visible_turns', False)
        self.record_shared_experiences = self._cfg_bool('conversation.record_shared_experiences', True)
        self.custom_system_prompt = self._cfg_str('conversation.system_prompt', '')
        self._avatar_cache_dir = self.data_dir / 'avatar'
        self._avatar_cache_dir.mkdir(parents=True, exist_ok=True)
        self._avatar_fallback_log_key = ''
        self.stt_mode = self._normalize_stt_mode(self._cfg_str('speech.stt_mode', 'auto'))
        self.stt_provider_id = self._cfg_str('speech.stt_provider_id', '')
        self.stt_correction_enabled = self._cfg_bool('speech.stt_correction_enabled', True)
        self.browser_language = self._cfg_str('speech.browser_language', 'zh-CN') or 'zh-CN'
        self.tts_provider_id = self._cfg_str('speech.tts_provider_id', '')
        self.browser_tts_fallback = self._cfg_bool('speech.browser_tts_fallback', True)
        self.direct_multilingual_tts = self._cfg_bool('speech.direct_multilingual_tts', True)
        self.tts_timeout_seconds = _clamp_int(self._cfg('speech.tts_timeout_seconds', 60), 60, 15, 180)
        self.tts_volume_ratio = _clamp_float(self._cfg('speech.tts_volume_percent', 100), 100.0, 0.0, 100.0) / 100.0
        self.realtime_duplex_enabled = self._cfg_bool('speech.realtime_duplex_enabled', False)
        self.watch_auto_comment = self._cfg_bool('watch.auto_comment', True)
        self.watch_prepare_knowledge = self._cfg_bool('watch.prepare_knowledge', True)
        self.watch_comment_interval_seconds = _clamp_int(self._cfg('watch.comment_interval_seconds', 60), 60, 20, 600)
        self.watch_scene_min_interval_seconds = _clamp_int(self._cfg('watch.scene_min_interval_seconds', 18), 18, 8, 120)
        self.watch_memory_refresh_seconds = _clamp_int(self._cfg('watch.memory_refresh_seconds', 240), 240, 90, 900)
        self.watch_duck_video_volume = self._cfg_bool('watch.duck_video_volume', True)
        self.watch_duck_volume_ratio = _clamp_float(self._cfg('watch.duck_volume_percent', 28), 28.0, 5.0, 80.0) / 100.0
        self.novel_library_enabled = self._cfg_bool('library.enabled', True)
        self.novel_import_admin_only = self._cfg_bool('library.import_admin_only', True)
        self.novel_max_upload_mb = _clamp_int(self._cfg('library.max_upload_mb', 40), 40, 1, 200)
        self.novel_retrieval_top_k = _clamp_int(self._cfg('novel.retrieval_top_k', 8), 8, 3, 20)
        self.novel_answer_with_sources = self._cfg_bool('novel.answer_with_sources', True)
        self.novel_spoiler_policy = self._cfg_str('novel.spoiler_policy', 'strict') or 'strict'
        self.novel_auto_save_progress = self._cfg_bool('novel.auto_save_progress', True)
        self.rooms: dict[str, RoomSession] = {}
        self.detached_rooms: dict[str, RoomSession] = {}
        self.room_resume_grace_seconds = 180
        self.media_sources: dict[str, ResolvedMedia] = {}
        self._scene_cache: dict[str, Any] = {'at': 0.0, 'user_id': '', 'scene': {}}
        self._identity_cache: dict[str, Any] = {'at': 0.0, 'identity': {}}
        self._provider_warn_ids: set[str] = set()
        self._ffmpeg_missing_warned = False
        self._astrbot_room_conversations: dict[str, tuple[str, str]] = {}
        self._stt_correction_cache: dict[tuple[str, str, tuple[str, ...]], tuple[float, str]] = {}
        self._persona_cache: dict[str, Any] = {'at': 0.0, 'key': '', 'prompt': ''}
        self._bilibili_runtime_state: dict[str, Any] = {'at': 0.0, 'linked': False, 'valid': None}
        self._background_tasks: set[asyncio.Task] = set()
        self.token_usage = TokenUsageTracker(self.data_dir / 'together_companion_token_usage.json')
        self.token_bridge = TogetherCompanionTokenBridge(self)
        self.extension_api = TogetherCompanionExtensionAPI(self)
        self._native_provider = None
        self.media_resolver = BilibiliMediaResolver(self.data_dir)
        self.novel_library = NovelLibrary(self.data_dir / 'novel_library', max_upload_mb=self.novel_max_upload_mb)
        self._native_novel_libraries: dict[str, NovelLibrary] = {}
        self.room_server = TogetherRoomServer(self, host=self.server_host, port=self.server_port, web_root=self.plugin_root / 'web', resource_package=__package__ or PLUGIN_NAME)
        data_root = self.data_dir
        self.quick_tunnel = CloudflareQuickTunnel(local_url=self.room_server.local_base_url, search_paths=[data_root / 'tools' / 'bin', self.plugin_root / 'tools'])
        pass

    async def terminate(self) -> None:
        native = self._native_provider
        self._native_provider = None
        if native is not None:
            await native.close()
        for room in list(self.rooms.values()):
            await self.close_room(room)
            try:
                if room.websocket is not None:
                    await room.websocket.close(code=1001, message='插件正在重载'.encode('utf-8'))
            except Exception as exc:
                logger.debug('[TogetherCompanion] 关闭房间连接失败: %s', exc)
        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self.media_sources.clear()
        await self.quick_tunnel.stop()
        await self.room_server.stop()
        await self._cleanup_temp_files()
        self.token_usage.save(force=True)

    def build_native(self, version):
        from companion_v2.together.provider import NativeTogetherProvider
        self._native_provider = NativeTogetherProvider(self, self.data_dir, version=version)
        return self._native_provider

    async def initialize(self):
        if self.server_enabled:
            await self._start_room_server()
