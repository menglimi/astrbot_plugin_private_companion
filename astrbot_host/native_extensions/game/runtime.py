"""V2-owned game lifecycle; no Star registration."""

from __future__ import annotations

from .common import *
from ..service import NativeService
from ..usage import TokenUsageTracker, flush_model_usage
from .settings_methods import GameSettingsMethods
from .rooms_methods import GameRoomsMethods
from .media_methods import GameMediaMethods
from .activity_methods import GameActivityMethods
from .model_methods import GameModelMethods

class GameRuntime(NativeService, GameSettingsMethods, GameRoomsMethods, GameMediaMethods, GameActivityMethods, GameModelMethods):

    def __init__(self, context: Context, config: AstrBotConfig, data_root, controller) -> None:
        self.context = context
        self.controller = controller
        self.config = config or {}
        self.plugin_root = Path(__file__).resolve().parent
        self.data_dir = Path(data_root)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.token_usage = TokenUsageTracker(self.data_dir / 'game_token_usage.json',
                                             source='game', display_name='游戏棋桌')
        self._model_usage_tasks = set()
        self.server_enabled = self._cfg_bool('server.enabled', True)
        self.native_v2_tictactoe_enabled = self._cfg_bool('native_v2.tictactoe_enabled', True)
        self.native_v2_gomoku_enabled = self._cfg_bool('native_v2.gomoku_enabled', True)
        self.native_v2_pig_dice_enabled = self._cfg_bool('native_v2.pig_dice_enabled', True)
        self.pig_dice_target_score = self._cfg_int('pig_dice.target_score', 50, minimum=20, maximum=200)
        self.native_v2_pig_dice_target_score = self._cfg_int(
            'native_v2.pig_dice_target_score', self.pig_dice_target_score, minimum=20, maximum=200
        )
        self.pig_dice_target_score = self.native_v2_pig_dice_target_score
        self.native_v2_blackjack_enabled = self._cfg_bool('native_v2.blackjack_enabled', True)
        self.native_v2_xiangqi_enabled = self._cfg_bool('native_v2.xiangqi_enabled', True)
        self.native_v2_turtle_soup_enabled = self._cfg_bool('native_v2.turtle_soup_enabled', True)
        legacy_hints = self._cfg_int('turtle_soup.max_hints', 3, minimum=0, maximum=8)
        self.native_v2_turtle_soup_max_hints = self._cfg_int('native_v2.turtle_soup_max_hints', legacy_hints, minimum=0, maximum=8)
        legacy_level = normalize_content_level(self._cfg('turtle_soup.content_level', 'normal'))
        self.native_v2_turtle_soup_content_level = normalize_content_level(self._cfg('native_v2.turtle_soup_content_level', legacy_level))
        self.native_v2_draw_guess_enabled = self._cfg_bool('native_v2.draw_guess_enabled', True)
        legacy_guesses = self._cfg_int('draw_guess.max_guesses', 5, minimum=1, maximum=10)
        self.native_v2_draw_guess_max_guesses = self._cfg_int('native_v2.draw_guess_max_guesses', legacy_guesses, minimum=1, maximum=10)
        legacy_duration = self._cfg_int('draw_guess.duration_seconds', 120, minimum=10, maximum=600)
        self.native_v2_draw_guess_duration_seconds = self._cfg_int('native_v2.draw_guess_duration_seconds', legacy_duration, minimum=10, maximum=600)
        self.server_host = self._cfg_str('server.host', '127.0.0.1') or '127.0.0.1'
        self.access_host = self._cfg_str('server.access_host', '')
        self.server_port = self._cfg_int('server.port', 6331, minimum=1, maximum=65535)
        self.public_base_url = self._validated_public_url(self._cfg_str('server.public_base_url', ''))
        self.external_base_url = self._validated_external_url(self._cfg_str('server.external_base_url', ''))
        self.auto_quick_tunnel = self._cfg_bool('server.auto_quick_tunnel', True)
        self.cloudflared_path = self._cfg_str('server.cloudflared_path', '')
        self.cloudflared_download_proxy = self._cfg_str('server.cloudflared_download_proxy', '')
        self.allow_cloudflared_download = self._cfg_bool('server.allow_cloudflared_download', True)
        self.log_level = self._cfg_str('logging.level', 'inherit').lower() or 'inherit'
        self._apply_log_level()
        self.trusted_browser_requested = self._cfg_bool('identity.enable_trusted_browser', False)
        self.trusted_browser_ttl_days = self._cfg_int('identity.trusted_browser_ttl_days', 30, minimum=1, maximum=365)
        configured_access = self._configured_access_base()
        self.trusted_browser_enabled = bool(self.trusted_browser_requested and configured_access and (urlsplit(configured_access).scheme == 'https'))
        public_path = urlsplit(configured_access).path.rstrip('/')
        self.trusted_browser_cookie_path = public_path or '/'
        self.trusted_identity_store = TrustedIdentityStore(self.data_dir / 'trusted_browsers.json', ttl_days=self.trusted_browser_ttl_days)
        if self.trusted_browser_requested and (not self.trusted_browser_enabled):
            logger.warning('[GameCompanion] 受信任浏览器需要有效的 HTTPS 外部地址，当前已自动禁用')
        self.group_rooms_enabled = self._cfg_bool('rooms.enable_group_rooms', True)
        self.private_rooms_enabled = self._cfg_bool('rooms.enable_private_rooms', True)
        self.allow_non_admin_group_creation = self._cfg_bool('rooms.allow_non_admin_group_creation', False)
        self.game_admin_ids = self._parse_qq_ids(self._cfg('rooms.game_admin_qq_ids', ''))
        self.record_shared_experience = self._cfg_bool('memory.record_shared_experience', True)
        self.private_qq_game_context_enabled = self._cfg_bool('context.enable_private_qq_game_context', True)
        self.recent_game_result_ttl_seconds = self._cfg_int('context.recent_game_result_ttl_minutes', 30, minimum=0, maximum=24 * 60) * 60
        self.commentary_cooldown = self._cfg_int('game.commentary_cooldown_seconds', 45, minimum=10, maximum=600)
        self.enabled_games: dict[GameType, bool] = {game_type: self._cfg_bool(f'{game_type}.enabled', True) for game_type in SUPPORTED_GAMES}
        self.turtle_soup_max_hints = self.native_v2_turtle_soup_max_hints
        self.turtle_soup_content_level = self.native_v2_turtle_soup_content_level
        self.turtle_soup_max_players = self._cfg_non_negative('turtle_soup.max_players', 6)
        self.draw_guess_vision_provider_id = self._cfg_str('draw_guess.vision_provider_id', '')
        self.draw_guess_max_guesses = self.native_v2_draw_guess_max_guesses
        self.draw_guess_duration_seconds = self.native_v2_draw_guess_duration_seconds
        self.blackjack_max_players = self._cfg_int('blackjack.max_players', 1, minimum=1, maximum=6)
        self.multiplayer_turn_timeout = self._cfg_non_negative('multiplayer.turn_timeout_seconds', 60)
        self.swap_request_cooldown = self._cfg_non_negative('multiplayer.swap_request_cooldown_seconds', 30)
        self.swap_request_expiry = self._cfg_int('multiplayer.swap_request_expiry_seconds', 20, minimum=1, maximum=600)
        self.xiangqi_engine = PikafishService(data_dir=self.data_dir, configured_path=self._cfg_str('xiangqi.engine_path', ''), download_proxy=self._cfg_str('xiangqi.download_proxy', ''), allow_download=self._cfg_bool('xiangqi.allow_engine_download', True), auto_download=self._cfg_bool('xiangqi.auto_download_engine', False))
        self.manager = RoomManager(max_group_rooms=self._cfg_non_negative('rooms.max_group_rooms', 1), max_private_rooms=self._cfg_non_negative('rooms.max_private_rooms', 1), empty_player_timeout=self._cfg_non_negative('rooms.empty_player_timeout_seconds', 60), idle_timeout=self._cfg_non_negative('rooms.idle_timeout_seconds', 300), turtle_soup_max_hints=self.turtle_soup_max_hints, turtle_soup_content_level=self.turtle_soup_content_level, turtle_soup_max_players=self.turtle_soup_max_players, multiplayer_turn_timeout=self.multiplayer_turn_timeout, swap_request_cooldown=self.swap_request_cooldown, swap_request_expiry=self.swap_request_expiry, draw_guess_max_guesses=self.draw_guess_max_guesses, draw_guess_duration_seconds=self.draw_guess_duration_seconds, pig_dice_target_score=self.pig_dice_target_score, blackjack_max_players=self.blackjack_max_players, enabled_games=self.enabled_games, xiangqi_engine=self.xiangqi_engine, event_callback=self._on_room_event)
        self.room_server = GameRoomServer(self, self.manager, host=self.server_host, port=self.server_port, web_root=self.plugin_root / 'web')
        self.quick_tunnel = QuickTunnel(self.room_server.local_base_url, search_paths=[self.data_dir.parent.parent / 'tools' / 'bin', self.plugin_root / 'tools'], configured_path=self.cloudflared_path, download_dir=self.data_dir / 'tools' / 'bin', download_proxy=self.cloudflared_download_proxy, allow_download=self.allow_cloudflared_download)
        self._watchdog_task: asyncio.Task | None = None
        self._tunnel_recovery_task: asyncio.Task | None = None
        self._next_tunnel_retry_at = 0.0
        self._background_tasks: set[asyncio.Task] = set()
        self._commentary_tasks: dict[str, asyncio.Task] = {}
        self._recent_private_game_results: dict[str, _RecentPrivateGameResult] = {}
        self._settings_lock = asyncio.Lock()
        self._native_provider = None
        self.extension_api = GameCompanionExtensionAPI(self)
        self._register_page_api()

    async def terminate(self) -> None:
        """Invalidate every room and stop only plugin-owned resources."""
        if self._native_provider is not None:
            await self._native_provider.close()
            self._native_provider = None
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
            self._watchdog_task = None
        await self.manager.close_all('AstrBot 或游戏插件已重载')
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        await flush_model_usage(self)
        await self.quick_tunnel.stop()
        await self.room_server.stop()
        await self.xiangqi_engine.close()
        logger.info('[GameCompanion] 所有运行态房间均已销毁')

    def build_native(self, version):
        from companion_v2.games.native.provider import NativeGameProvider
        self._native_provider = NativeGameProvider(self, self.data_dir, version=version)
        return self._native_provider

    async def initialize(self):
        if self.server_enabled:
            await self.room_server.start()
