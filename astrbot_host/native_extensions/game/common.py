from __future__ import annotations
import asyncio
import base64
import binascii
import inspect
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context
from astrbot.api.web import request
from companion_v2.games.blackjack import BlackjackGame
from companion_v2.games.draw_guess import DrawGuessGame
from companion_v2.games.gomoku import BLACK as GOMOKU_BLACK
from companion_v2.games.gomoku import Difficulty, GomokuGame
from companion_v2.games.models import GameRoom, GameType, TurtleSoupMode, Visitor
from companion_v2.games.pig_dice import PigDiceGame
from companion_v2.games.pikafish import PikafishService
from companion_v2.games.room_manager import SUPPORTED_GAMES, RoomManager
from .server import GameRoomServer
from companion_v2.games.tictactoe import NOUGHT as TICTACTOE_NOUGHT
from companion_v2.games.tictactoe import TicTacToeGame
from companion_v2.games.tictactoe import X as TICTACTOE_X
from companion_v2.games.trusted_identity import TrustedIdentityStore
from .tunnel import QuickTunnel
from companion_v2.games.turtle_soup import VERDICT_LABELS, TurtleSoupGame, fallback_puzzle, normalize_content_level, puzzle_from_mapping
from companion_v2.games.turtle_soup_ai import answer_judge_prompt, extract_json_object, generation_prompt, parse_answer_judgment, parse_question_judgment, parse_reverse_turn, public_judge_history, question_judge_prompt, reverse_public_history, reverse_turn_prompt, validation_passed, validation_prompt
from companion_v2.games.xiangqi import BLACK as XIANGQI_BLACK
from companion_v2.games.xiangqi import RED as XIANGQI_RED
from companion_v2.games.xiangqi import XiangqiGame
PLUGIN_NAME = 'astrbot_plugin_private_companion.game'
PLUGIN_VERSION = '2.0.0'
PAGE_API_PREFIX = f'/{PLUGIN_NAME}/page'
GAME_CATALOG: tuple[dict[str, Any], ...] = ({'game_type': 'gomoku', 'label': '五子棋', 'description': '15×15 棋盘，由 Bot 人格决定三档棋力。', 'fields': ()}, {'game_type': 'xiangqi', 'label': '中国象棋', 'description': '使用独立 Pikafish 引擎进行对局。', 'fields': ({'key': 'allow_engine_download', 'config_key': 'xiangqi.allow_engine_download', 'label': '允许管理台下载引擎', 'type': 'bool', 'default': True, 'hint': '关闭后管理台不能安装或更新 Pikafish。'}, {'key': 'auto_download_engine', 'config_key': 'xiangqi.auto_download_engine', 'label': '首次使用时自动下载', 'type': 'bool', 'default': False, 'hint': '推荐保持关闭，由管理员先在管理台确认安装。'})}, {'game_type': 'tictactoe', 'label': '井字棋', 'description': '3×3 棋盘，由 Bot 人格决定三档棋力。', 'fields': ()}, {'game_type': 'turtle_soup', 'label': '海龟汤', 'description': '支持 Bot 出题、玩家出题和多人轮流参与。', 'fields': ({'key': 'max_hints', 'config_key': 'turtle_soup.max_hints', 'label': '每题最多提示', 'type': 'int', 'default': 3, 'minimum': 0, 'maximum': 8, 'unit': '次', 'hint': '0 表示允许依次查看全部预生成提示。'}, {'key': 'content_level', 'config_key': 'turtle_soup.content_level', 'label': '题目内容等级', 'type': 'select', 'default': 'normal', 'options': ({'value': 'all_ages', 'label': '全年龄'}, {'value': 'normal', 'label': '普通'}, {'value': 'unrestricted', 'label': '不限制'}), 'hint': '仍会遵守模型和平台安全限制。'}, {'key': 'max_players', 'config_key': 'turtle_soup.max_players', 'label': '最大玩家席', 'type': 'int', 'default': 6, 'minimum': 0, 'maximum': 100, 'unit': '人', 'hint': '0 表示不限制人数。'}, {'key': 'turn_timeout_seconds', 'config_key': 'multiplayer.turn_timeout_seconds', 'label': '单人回合时间', 'type': 'int', 'default': 60, 'minimum': 0, 'maximum': 3600, 'unit': '秒', 'hint': '0 表示关闭回合倒计时。'}, {'key': 'swap_request_cooldown_seconds', 'config_key': 'multiplayer.swap_request_cooldown_seconds', 'label': '交换申请冷却', 'type': 'int', 'default': 30, 'minimum': 0, 'maximum': 3600, 'unit': '秒', 'hint': '0 表示不限制申请频率。'}, {'key': 'swap_request_expiry_seconds', 'config_key': 'multiplayer.swap_request_expiry_seconds', 'label': '交换申请有效期', 'type': 'int', 'default': 20, 'minimum': 1, 'maximum': 600, 'unit': '秒', 'hint': '过期申请会自动清理。'})}, {'game_type': 'pig_dice', 'label': '贪心骰子', 'description': '继续冒险或及时收手，由 Bot 人格决定风险倾向。', 'fields': ({'key': 'target_score', 'config_key': 'pig_dice.target_score', 'label': '获胜目标分数', 'type': 'int', 'default': 50, 'minimum': 20, 'maximum': 200, 'unit': '分', 'hint': '新一局中先达到目标分数的一方获胜。'},)}, {'game_type': 'draw_guess', 'label': '你画我猜', 'description': '用户作画，Bot 使用视觉模型猜图。', 'fields': ({'key': 'vision_provider_id', 'config_key': 'draw_guess.vision_provider_id', 'label': '视觉模型 Provider ID', 'type': 'string', 'default': '', 'maximum_length': 200, 'hint': '留空时使用当前会话模型。'}, {'key': 'duration_seconds', 'config_key': 'draw_guess.duration_seconds', 'label': '作画倒计时', 'type': 'int', 'default': 120, 'minimum': 10, 'maximum': 600, 'unit': '秒', 'hint': '倒计时结束后本轮自动结算。'}, {'key': 'max_guesses', 'config_key': 'draw_guess.max_guesses', 'label': 'Bot 最大猜测次数', 'type': 'int', 'default': 5, 'minimum': 1, 'maximum': 10, 'unit': '次', 'hint': '每次点击让 Bot 猜都会消耗一次。'})}, {'game_type': 'blackjack', 'label': '二十一点', 'description': '用户当闲家、Bot 当庄家比点数；支持 1-6 位玩家各自对庄。', 'fields': ({'key': 'max_players', 'config_key': 'blackjack.max_players', 'label': '最大玩家席', 'type': 'int', 'default': 1, 'minimum': 1, 'maximum': 6, 'unit': '人', 'hint': '默认 1 人，即用户单独和 Bot 庄家对局。'},)})

@dataclass(slots=True)
class _RecentPrivateGameResult:
    room_id: str
    user_qq: str
    summary: str
    expires_at: float = 0.0

class GameCompanionExtensionAPI:
    """Public inventory without starting the lazy game server or a room."""

    def __init__(self, plugin: 'GameCompanionPlugin') -> None:
        self._plugin = plugin

    def companion_v2_status(self) -> dict[str, Any]:
        return {'schema': 'companion.provider-status.v1', 'plugin_id': PLUGIN_NAME, 'enabled': getattr(self._plugin, 'native_v2_tictactoe_enabled', False) or getattr(self._plugin, 'native_v2_gomoku_enabled', False) or getattr(self._plugin, 'native_v2_pig_dice_enabled', False) or getattr(self._plugin, 'native_v2_blackjack_enabled', False) or getattr(self._plugin, 'native_v2_xiangqi_enabled', False) or getattr(self._plugin, 'native_v2_turtle_soup_enabled', False) or getattr(self._plugin, 'native_v2_draw_guess_enabled', False), 'runtime_ready': self._plugin._native_provider is not None}

    def companion_v2_native(self):
        return self._plugin._native_provider if getattr(self._plugin, 'native_v2_tictactoe_enabled', False) or getattr(self._plugin, 'native_v2_gomoku_enabled', False) or getattr(self._plugin, 'native_v2_pig_dice_enabled', False) or getattr(self._plugin, 'native_v2_blackjack_enabled', False) or getattr(self._plugin, 'native_v2_xiangqi_enabled', False) or getattr(self._plugin, 'native_v2_turtle_soup_enabled', False) or getattr(self._plugin, 'native_v2_draw_guess_enabled', False) else None

    def page_snapshot(self) -> dict[str, Any]:
        return self._plugin.management_snapshot()

    async def page_action(self, action_name: str, payload: dict[str, Any], current) -> dict[str, Any]:
        return await self._plugin.management_action(action_name, payload, current)

__all__ = [name for name in globals() if not name.startswith("__")]
