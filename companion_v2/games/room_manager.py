from __future__ import annotations

import asyncio
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from .blackjack import BlackjackGame
from .draw_guess import DrawGuessGame
from .gomoku import BLACK, WHITE, Difficulty, GomokuGame
from .models import (
    GameRoom,
    GameType,
    MultiplayerState,
    PlayerSeat,
    RoomSource,
    SeatSwapRequest,
    TurtleSoupMode,
    Visitor,
)
from .pig_dice import PigDiceGame
from .pikafish import PikafishService
from .tictactoe import NOUGHT as TICTACTOE_NOUGHT
from .tictactoe import TicTacToeGame
from .tictactoe import X as TICTACTOE_X
from .trusted_identity import TrustedIdentity
from .turtle_soup import (
    SoupContentLevel,
    SoupPuzzle,
    SoupVerdict,
    TurtleSoupGame,
    clean_player_text,
)
from .xiangqi import BLACK as XIANGQI_BLACK
from .xiangqi import RED as XIANGQI_RED
from .xiangqi import XiangqiGame

RoomCallback = Callable[[str, GameRoom, dict[str, Any]], Awaitable[None]]

SUPPORTED_GAMES: tuple[GameType, ...] = (
    "gomoku",
    "xiangqi",
    "tictactoe",
    "turtle_soup",
    "pig_dice",
    "draw_guess",
    "blackjack",
)


class RoomManager:
    """Own room quotas, seats, game transitions, and expiry rules."""

    CLOSED_ACCESS_TTL_SECONDS = 15 * 60
    MAX_CLOSED_ACCESS_RECORDS = 256
    FINISHED_PLAYER_LEAVE_GRACE_SECONDS = 8
    FINISHED_PLAYER_HEARTBEAT_TIMEOUT_SECONDS = 60
    IDENTITY_TOKEN_TTL_SECONDS = 300
    CHAT_COOLDOWN_SECONDS = 0.8

    def __init__(
        self,
        *,
        max_group_rooms: int = 1,
        max_private_rooms: int = 1,
        empty_player_timeout: int = 60,
        idle_timeout: int = 300,
        turtle_soup_max_hints: int = 3,
        turtle_soup_content_level: SoupContentLevel = "normal",
        turtle_soup_max_players: int = 6,
        multiplayer_turn_timeout: int = 60,
        swap_request_cooldown: int = 30,
        swap_request_expiry: int = 20,
        draw_guess_max_guesses: int = 5,
        draw_guess_duration_seconds: int = 120,
        pig_dice_target_score: int = 50,
        blackjack_max_players: int = 1,
        enabled_games: Mapping[GameType, bool] | None = None,
        xiangqi_engine: PikafishService | None = None,
        event_callback: RoomCallback | None = None,
    ) -> None:
        self.max_group_rooms = max(0, int(max_group_rooms))
        self.max_private_rooms = max(0, int(max_private_rooms))
        self.empty_player_timeout = max(0, int(empty_player_timeout))
        self.idle_timeout = max(0, int(idle_timeout))
        self.turtle_soup_max_hints = max(0, int(turtle_soup_max_hints))
        self.turtle_soup_content_level = turtle_soup_content_level
        self.turtle_soup_max_players = max(0, int(turtle_soup_max_players))
        self.multiplayer_turn_timeout = max(0, int(multiplayer_turn_timeout))
        self.swap_request_cooldown = max(0, int(swap_request_cooldown))
        self.swap_request_expiry = max(1, int(swap_request_expiry))
        self.draw_guess_max_guesses = max(1, min(int(draw_guess_max_guesses), 10))
        self.draw_guess_duration_seconds = max(
            10, min(int(draw_guess_duration_seconds), 600)
        )
        self.pig_dice_target_score = max(20, min(int(pig_dice_target_score), 200))
        self.blackjack_max_players = max(1, min(int(blackjack_max_players), 6))
        configured_games = enabled_games or {}
        self.enabled_games: dict[GameType, bool] = {
            game_type: bool(configured_games.get(game_type, True))
            for game_type in SUPPORTED_GAMES
        }
        self.xiangqi_engine = xiangqi_engine
        self.event_callback = event_callback
        self.rooms: dict[str, GameRoom] = {}
        self._access_index: dict[str, str] = {}
        self._closed_access: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def create_room(
        self,
        *,
        source: RoomSource,
        session_id: str,
        platform: str,
        group_id: str,
        creator_qq: str,
        creator_name: str,
        admin_room: bool,
        game_type: GameType = "gomoku",
        difficulty: Difficulty,
        turtle_soup_mode: TurtleSoupMode = "bot_host",
    ) -> GameRoom:
        """Create a room atomically under the source-wide quota."""
        self.require_game_enabled(game_type)
        async with self._lock:
            limit = (
                self.max_group_rooms if source == "group" else self.max_private_rooms
            )
            active_count = sum(room.source == source for room in self.rooms.values())
            if limit and active_count >= limit:
                label = "群聊" if source == "group" else "私聊"
                raise ValueError(f"{label}房间已达到并行上限 {limit}")
            room_id = secrets.token_hex(4)
            while room_id in self.rooms:
                room_id = secrets.token_hex(4)
            access_token = secrets.token_urlsafe(32)
            room = GameRoom(
                room_id=room_id,
                access_token=access_token,
                source=source,
                session_id=session_id,
                platform=platform,
                group_id=group_id,
                creator_qq=creator_qq,
                creator_name=creator_name,
                admin_room=admin_room,
                game_type=game_type,
                difficulty=difficulty,
                turtle_soup_mode=turtle_soup_mode,
            )
            self._configure_multiplayer(room)
            self.rooms[room_id] = room
            self._access_index[access_token] = room_id
            return room

    def by_access_token(self, access_token: str) -> GameRoom | None:
        """Resolve an unguessable public room token."""
        room_id = self._access_index.get(str(access_token or ""))
        return self.rooms.get(room_id or "")

    def closed_reason_by_access_token(self, access_token: str) -> str:
        """Return a short-lived close reason without retaining room data."""
        self._purge_closed_access()
        record = self._closed_access.get(str(access_token or ""))
        return record[0] if record else ""

    def for_session(self, session_id: str) -> list[GameRoom]:
        """Return rooms attached to one real AstrBot conversation."""
        return [room for room in self.rooms.values() if room.session_id == session_id]

    async def join(
        self,
        room: GameRoom,
        visitor_token: str = "",
        *,
        trusted_identity: TrustedIdentity | None = None,
    ) -> Visitor:
        """Resume a browser identity or assign the next stable room number."""
        async with room.lock:
            visitor = room.visitors.get(str(visitor_token or ""))
            if trusted_identity is not None:
                existing = next(
                    (
                        item
                        for item in room.visitors.values()
                        if item.identity_confirmed and item.qq == trusted_identity.qq
                    ),
                    None,
                )
                if existing is not None:
                    visitor = existing
                elif visitor is None or (
                    visitor.identity_confirmed
                    and visitor.qq != trusted_identity.qq
                ):
                    visitor = Visitor(number=room.next_visitor_number)
                    room.next_visitor_number += 1
                    room.visitors[visitor.token] = visitor
                self._confirm_visitor_identity(
                    room,
                    visitor,
                    trusted_identity.qq,
                    trusted_identity.display_name,
                    allow_trusted_enrollment=False,
                )
            elif visitor is None:
                visitor = Visitor(number=room.next_visitor_number)
                room.next_visitor_number += 1
                room.visitors[visitor.token] = visitor
            visitor.connected = True
            visitor.last_seen_at = time.time()
            visitor.left_at = None
            visitor.ensure_binding_token(ttl=self.IDENTITY_TOKEN_TTL_SECONDS)
            return visitor

    async def bind_visitor_identity(
        self,
        *,
        session_id: str,
        identity_token: str,
        qq: str,
        display_name: str = "",
    ) -> tuple[GameRoom, Visitor]:
        """Consume a browser challenge from the matching QQ conversation."""
        normalized_token = str(identity_token or "").strip().upper()
        normalized_qq = str(qq or "").strip()
        if not normalized_token:
            raise ValueError("身份令牌不能为空")
        if not normalized_qq.isdigit():
            raise ValueError("发送者 QQ 号无效")
        now = time.time()
        candidates = [
            room
            for room in self.rooms.values()
            if room.session_id == str(session_id or "")
        ]
        for room in candidates:
            async with room.lock:
                if room.admin_room:
                    continue
                visitor = next(
                    (
                        item
                        for item in room.visitors.values()
                        if item.binding_token
                        and secrets.compare_digest(
                            item.binding_token.upper(), normalized_token
                        )
                        and item.binding_expires_at > now
                    ),
                    None,
                )
                if visitor is None:
                    continue
                existing = next(
                    (
                        item
                        for item in room.visitors.values()
                        if item.identity_confirmed
                        and item.qq == normalized_qq
                        and item.token != visitor.token
                    ),
                    None,
                )
                if existing is not None:
                    raise ValueError("这个 QQ 已经绑定了本房间的其他访客")
                self._confirm_visitor_identity(
                    room,
                    visitor,
                    normalized_qq,
                    display_name,
                    allow_trusted_enrollment=True,
                )
                room.touch()
                return room, visitor
        raise ValueError("身份令牌无效、已使用或已过期")

    async def require_visitor_identity(
        self, room: GameRoom, visitor_token: str
    ) -> Visitor:
        """Require a browser visitor to have completed QQ binding."""
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if not visitor.identity_confirmed:
                raise PermissionError("请先在 QQ 中绑定页面令牌，再进入玩家席")
            return visitor

    async def heartbeat(self, room: GameRoom, visitor_token: str) -> Visitor:
        """Refresh presence without extending the room activity deadline."""
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            visitor.connected = True
            visitor.last_seen_at = time.time()
            visitor.left_at = None
            visitor.ensure_binding_token(ttl=self.IDENTITY_TOKEN_TTL_SECONDS)
            return visitor

    async def leave(self, room: GameRoom, visitor_token: str) -> None:
        """Record a browser departure without extending meaningful activity."""
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            visitor.connected = False
            visitor.left_at = time.time()

    async def begin_room_chat(
        self, room: GameRoom, visitor_token: str, text: str
    ) -> tuple[Visitor, str, bool, bool]:
        """Publish one UI chat message and return its authoritative room role."""
        cleaned = " ".join(str(text or "").strip().split())
        if not cleaned:
            raise ValueError("消息不能为空")
        if len(cleaned) > 500:
            raise ValueError("单条消息不能超过 500 个字符")
        now = time.time()
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if now - visitor.last_chat_at < self.CHAT_COOLDOWN_SECONDS:
                raise ValueError("发送得太快了，请稍后再试")
            visitor.last_chat_at = now
            is_player = self._is_player_token(room, visitor.token)
            is_current_player = visitor.token == (
                room.multiplayer.current_token
                if room.multiplayer.enabled
                else room.player_token
            )
            room.add_message("user", cleaned, visitor=visitor)
            room.record_chat_memory(visitor, "user", cleaned)
            room.touch()
            return visitor, cleaned, is_player, is_current_player

    async def add_room_chat_reply(
        self, room: GameRoom, visitor: Visitor, text: str, *, message_type: str = "chat"
    ) -> None:
        """Publish a Bot reply and privately associate it with the addressed QQ."""
        async with room.lock:
            if room.status == "closed" or room.room_id not in self.rooms:
                return
            room.add_message("bot", text, message_type=message_type)
            room.record_chat_memory(visitor, "bot", text)

    @staticmethod
    def _is_player_token(room: GameRoom, visitor_token: str) -> bool:
        if room.multiplayer.enabled:
            return room.multiplayer.seat_for_token(visitor_token) is not None
        return bool(room.player_token and room.player_token == visitor_token)

    async def claim_and_start(
        self, room: GameRoom, visitor_token: str, side: str
    ) -> None:
        """Claim a normal room's empty player seat and start the first game."""
        self.require_game_enabled(room.game_type)
        start_required = False
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if room.admin_room:
                raise ValueError("这个房间需要管理员从游戏管理台安排玩家")
            if room.multiplayer.enabled:
                if (
                    room.status == "active"
                    and isinstance(room.game, BlackjackGame)
                    and not room.game.finished
                ):
                    raise ValueError("本局进行中，请等这一局结束后再加入玩家席")
                if room.multiplayer.seat_for_token(visitor.token) is not None:
                    raise ValueError("你已经在玩家席")
                if (
                    room.multiplayer.capacity
                    and len(room.multiplayer.seats) >= room.multiplayer.capacity
                ):
                    raise ValueError("玩家席已经坐满")
                first = not room.multiplayer.seats
                room.multiplayer.seats.append(
                    PlayerSeat(
                        visitor_token=visitor.token,
                        qq=visitor.qq if visitor.identity_confirmed else "",
                        display_name=visitor.display_name,
                        identity_confirmed=visitor.identity_confirmed,
                    )
                )
                if visitor.identity_confirmed:
                    room.confirmed_participant_qqs.add(visitor.qq)
                    room.participant_names[visitor.qq] = visitor.display_name
                self._sync_primary_player(room)
                room.player_empty_since = None
                room.touch()
                if first:
                    room.status = "setup"
                    start_required = not (
                        room.game_type == "blackjack"
                        and room.multiplayer.capacity > 1
                    )
                else:
                    room.add_message(
                        "system", f"{self._visitor_label(visitor)}加入了玩家席。"
                    )
                self._reset_turn_deadline(room)
            else:
                if room.player_token and room.player_token != visitor.token:
                    raise ValueError("玩家席已经有人，请联系创建者处理")
                if room.player_seat_locked and room.player_token != visitor.token:
                    raise ValueError("玩家席已由创建者锁定")
                room.player_token = visitor.token
                room.player_qq = visitor.qq if visitor.identity_confirmed else ""
                room.player_identity_confirmed = visitor.identity_confirmed
                if visitor.identity_confirmed:
                    room.confirmed_participant_qqs.add(visitor.qq)
                    room.participant_names[visitor.qq] = visitor.display_name
                room.player_empty_since = None
                room.status = "setup"
                room.touch()
                start_required = True
        if start_required:
            await self.start_game(room, visitor_token, side)
        else:
            await self._emit("seats_changed", room, {"joined": visitor.number})

    async def assign_player(
        self,
        room: GameRoom,
        visitor_number: int,
        player_qq: str,
        *,
        allow_non_numeric: bool = False,
    ) -> None:
        """Assign an administrator-reviewed visitor and QQ identity."""
        async with room.lock:
            visitor = self._visitor_by_number(room, visitor_number)
            player_qq = str(player_qq or "").strip()
            if not player_qq:
                raise ValueError("玩家身份不能为空")
            if not allow_non_numeric and not player_qq.isdigit():
                raise ValueError("玩家 QQ 号必须只包含数字")
            if any(
                item.identity_confirmed
                and item.qq == player_qq
                and item.token != visitor.token
                for item in room.visitors.values()
            ):
                raise ValueError("这个 QQ 已经绑定了本房间的其他访客")
            if room.multiplayer.enabled:
                seat = room.multiplayer.seat_for_token(visitor.token)
                if seat is None:
                    if (
                        room.multiplayer.capacity
                        and len(room.multiplayer.seats) >= room.multiplayer.capacity
                    ):
                        raise ValueError("玩家席已经坐满")
                    seat = PlayerSeat(visitor_token=visitor.token)
                    room.multiplayer.seats.append(seat)
                seat.qq = player_qq
                seat.display_name = visitor.display_name
                seat.identity_confirmed = True
                visitor.qq = player_qq
                if not visitor.display_name and player_qq == room.creator_qq:
                    visitor.display_name = room.creator_name
                    seat.display_name = visitor.display_name
                visitor.identity_confirmed = True
                visitor.binding_token = ""
                visitor.binding_expires_at = 0.0
                room.confirmed_participant_qqs.add(player_qq)
                room.participant_names[player_qq] = visitor.display_name
                first = room.player_token == ""
                self._sync_primary_player(room)
                room.player_seat_locked = True
                room.player_empty_since = None
                if room.game is None and room.status == "waiting":
                    room.status = "setup"
                if first:
                    room.status = "setup"
                self._reset_turn_deadline(room)
                room.touch()
            else:
                room.player_token = visitor.token
                room.player_qq = player_qq
                room.player_identity_confirmed = True
                visitor.qq = player_qq
                if not visitor.display_name and player_qq == room.creator_qq:
                    visitor.display_name = room.creator_name
                visitor.identity_confirmed = True
                visitor.binding_token = ""
                visitor.binding_expires_at = 0.0
                room.confirmed_participant_qqs.add(player_qq)
                room.participant_names[player_qq] = visitor.display_name
                room.player_seat_locked = True
                room.player_empty_since = None
                room.game = None
                room.status = "setup"
                room.touch()
        await self._emit("player_confirmed", room, {})

    async def correct_creator(
        self, room: GameRoom, actor_qq: str, visitor_number: int
    ) -> None:
        """Reset a stolen normal room and swap the creator into the player seat."""
        async with room.lock:
            if room.admin_room:
                raise ValueError("管理员房间请从游戏管理台重新安排玩家")
            if str(actor_qq) != room.creator_qq:
                raise PermissionError("只有房间创建者能纠正玩家身份")
            visitor = self._visitor_by_number(room, visitor_number)
            if room.multiplayer.enabled:
                seats = room.multiplayer.seats
                target_index = next(
                    (
                        index
                        for index, seat in enumerate(seats)
                        if seat.visitor_token == visitor.token
                    ),
                    -1,
                )
                creator_seat = PlayerSeat(
                    visitor_token=visitor.token,
                    qq=room.creator_qq,
                    display_name=visitor.display_name,
                    identity_confirmed=True,
                )
                if seats:
                    previous_primary = seats[0]
                    seats[0] = creator_seat
                    if target_index > 0:
                        seats[target_index] = PlayerSeat(
                            visitor_token=previous_primary.visitor_token,
                            qq=room.visitors.get(previous_primary.visitor_token).qq
                            if room.visitors.get(previous_primary.visitor_token)
                            and room.visitors.get(previous_primary.visitor_token).identity_confirmed
                            else "",
                            display_name=room.visitors.get(previous_primary.visitor_token).display_name
                            if room.visitors.get(previous_primary.visitor_token)
                            else "",
                            identity_confirmed=bool(
                                room.visitors.get(previous_primary.visitor_token)
                                and room.visitors.get(previous_primary.visitor_token).identity_confirmed
                            ),
                        )
                else:
                    seats.append(creator_seat)
                room.multiplayer.current_turn_index = 0
                room.multiplayer.swap_requests.clear()
                self._sync_primary_player(room)
                room.confirmed_participant_qqs.add(room.creator_qq)
                room.participant_names[room.creator_qq] = (
                    visitor.display_name or room.creator_name
                )
            else:
                room.player_token = visitor.token
                room.player_qq = room.creator_qq
                room.player_identity_confirmed = True
                room.confirmed_participant_qqs.add(room.creator_qq)
                room.participant_names[room.creator_qq] = (
                    visitor.display_name or room.creator_name
                )
            room.player_seat_locked = True
            room.player_empty_since = None
            room.game = None
            room.status = "setup"
            room.touch()
            self._reset_turn_deadline(room)
            room.add_message(
                "system", f"身份已纠正：{visitor.number} 号成为玩家，对局已重置。"
            )
        await self._emit("player_confirmed", room, {"corrected": True})

    async def confirm_creator(self, room: GameRoom, actor_qq: str) -> None:
        """Confirm that the current browser player is the room creator."""
        async with room.lock:
            if str(actor_qq) != room.creator_qq:
                raise PermissionError("只有房间创建者能确认自己的身份")
            if not room.player_token:
                raise ValueError("当前还没有人在玩家席")
            if room.multiplayer.enabled:
                seat = room.multiplayer.seat_for_token(room.player_token)
                if seat is None:
                    raise ValueError("当前还没有主玩家席")
                seat.qq = room.creator_qq
                seat.display_name = room.creator_name
                seat.identity_confirmed = True
                player = room.visitors.get(seat.visitor_token)
                if player is not None:
                    player.qq = room.creator_qq
                    player.display_name = room.creator_name
                    player.identity_confirmed = True
                    player.binding_token = ""
                    player.binding_expires_at = 0.0
                room.confirmed_participant_qqs.add(room.creator_qq)
                room.participant_names[room.creator_qq] = room.creator_name
                self._sync_primary_player(room)
            else:
                room.player_qq = room.creator_qq
                room.player_identity_confirmed = True
                player = room.player
                if player is not None:
                    player.qq = room.creator_qq
                    player.display_name = room.creator_name
                    player.identity_confirmed = True
                    player.binding_token = ""
                    player.binding_expires_at = 0.0
                room.confirmed_participant_qqs.add(room.creator_qq)
                room.participant_names[room.creator_qq] = room.creator_name
            room.player_seat_locked = True
            room.touch()
        await self._emit("player_confirmed", room, {})

    async def start_game(self, room: GameRoom, visitor_token: str, side: str) -> None:
        """Start a game after a seat has been assigned."""
        self.require_game_enabled(room.game_type)
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if room.multiplayer.enabled:
                allowed = room.multiplayer.seat_for_token(visitor.token) is not None
            else:
                allowed = visitor.token == room.player_token
            if not allowed:
                raise PermissionError("只有当前玩家可以开始对局")
            if room.status not in {"setup", "finished", "rematch_pending"}:
                raise ValueError("当前房间状态不能开始新对局")
            if room.game_type == "xiangqi":
                engine = self._require_xiangqi_engine()
                normalized_side = str(side or "human_red").strip().lower()
                if normalized_side not in {"human_red", "human_black", "random"}:
                    normalized_side = "human_red"
                if normalized_side == "random":
                    normalized_side = secrets.choice(("human_red", "human_black"))
                human_side = (
                    XIANGQI_RED if normalized_side == "human_red" else XIANGQI_BLACK
                )
                room.game = await XiangqiGame.create(
                    engine,
                    human_side=human_side,
                    difficulty=room.difficulty,
                )
                side_label = "红" if human_side == XIANGQI_RED else "黑"
            elif room.game_type == "gomoku":
                normalized_side = str(side or "human_black").strip().lower()
                if normalized_side not in {"human_black", "bot_black", "random"}:
                    normalized_side = "human_black"
                if normalized_side == "random":
                    normalized_side = secrets.choice(("human_black", "bot_black"))
                human_color = BLACK if normalized_side == "human_black" else WHITE
                room.game = GomokuGame(
                    human_color=human_color, difficulty=room.difficulty
                )
                side_label = "黑" if human_color == BLACK else "白"
            elif room.game_type == "tictactoe":
                normalized_side = str(side or "human_x").strip().lower()
                if normalized_side not in {"human_x", "human_o", "random"}:
                    normalized_side = "human_x"
                if normalized_side == "random":
                    normalized_side = secrets.choice(("human_x", "human_o"))
                human_mark = (
                    TICTACTOE_X if normalized_side == "human_x" else TICTACTOE_NOUGHT
                )
                room.game = TicTacToeGame(
                    human_mark=human_mark, difficulty=room.difficulty
                )
                side_label = "X" if human_mark == TICTACTOE_X else "O"
            elif room.game_type == "pig_dice":
                room.game = PigDiceGame(
                    difficulty=room.difficulty,
                    target_score=self.pig_dice_target_score,
                )
                side_label = ""
            elif room.game_type == "draw_guess":
                room.game = DrawGuessGame(
                    difficulty=room.difficulty,
                    max_guesses=self.draw_guess_max_guesses,
                    duration_seconds=self.draw_guess_duration_seconds,
                )
                side_label = ""
            elif room.game_type == "blackjack":
                room.game = BlackjackGame.deal(
                    difficulty=room.difficulty,
                    player_numbers=[
                        room.visitors[seat.visitor_token].number
                        for seat in room.multiplayer.seats
                        if seat.visitor_token in room.visitors
                    ],
                )
                room.multiplayer.current_turn_index = 0
                side_label = ""
            else:
                room.game = TurtleSoupGame(
                    difficulty=room.difficulty,
                    max_hints=self.turtle_soup_max_hints,
                    content_level=self.turtle_soup_content_level,
                    mode=room.turtle_soup_mode,
                )
                side_label = ""
            room.status = "active"
            room.touch()
            if isinstance(room.game, TurtleSoupGame):
                room.add_message(
                    "system",
                    "Bot 正在准备一道新的海龟汤。"
                    if room.game.mode == "bot_host"
                    else "玩家出题模式开始，请当前玩家提供第一条公开线索。",
                )
            elif isinstance(room.game, PigDiceGame):
                first = "玩家" if room.game.turn == "human" else "Bot"
                room.add_message(
                    "system", f"新一局贪心骰子开始，由{first}先掷，目标 50 分。"
                )
            elif isinstance(room.game, DrawGuessGame):
                room.add_message(
                    "system",
                    f"你画我猜开始。玩家有 {room.game.duration_seconds} 秒作画，"
                    f"可让 Bot 猜 {room.game.max_guesses} 次。",
                )
            elif isinstance(room.game, BlackjackGame):
                room.add_message(
                    "system",
                    "新一局二十一点开始，Bot 是庄家。轮到当前玩家要牌或停牌。",
                )
            else:
                room.add_message(
                    "system",
                    f"新对局开始，玩家执{side_label}，Bot 使用{self._difficulty_label(room.difficulty)}棋力。",
                )
        if isinstance(room.game, TurtleSoupGame) and room.game.mode == "bot_host":
            await self._emit("soup_generation_requested", room, {})
            return
        if isinstance(room.game, TurtleSoupGame):
            async with room.lock:
                self._reset_turn_deadline(room)
        if isinstance(room.game, BlackjackGame):
            async with room.lock:
                if not room.game.finished:
                    self._advance_blackjack_turn(room)
                self._reset_turn_deadline(room)
            if room.game.finished:
                await self._finish_game(room)
                return
            if room.game.all_players_done():
                await self._blackjack_dealer_turn(room)
                return
        await self._emit("game_started", room, {})
        if self._is_bot_turn(room):
            await self._bot_turn(room)

    async def player_move(
        self,
        room: GameRoom,
        visitor_token: str,
        *,
        row: int = -1,
        column: int = -1,
        from_row: int = -1,
        from_column: int = -1,
        to_row: int = -1,
        to_column: int = -1,
        interaction: str = "click",
        color: int = 0,
        pair_row: int = -1,
        pair_column: int = -1,
        pair_color: int = 0,
    ) -> None:
        """Apply one browser move, optionally using the playful drag path."""
        board_event: dict[str, object] = {"actor": "human"}
        follow_up_event: dict[str, object] | None = None
        playful = str(interaction or "click").strip().lower() in {
            "drag", "drag_pair", "drag_assist"
        }
        paired = str(interaction or "").strip().lower() == "drag_pair"
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if visitor.token != room.player_token:
                raise PermissionError("当前浏览器不在玩家席")
            if room.status != "active" or room.game is None:
                raise ValueError("当前没有正在进行的对局")
            if isinstance(room.game, (TurtleSoupGame, PigDiceGame, DrawGuessGame)):
                raise ValueError("当前游戏不使用棋盘落子接口")
            if isinstance(room.game, XiangqiGame):
                await room.game.place_human(
                    self._require_xiangqi_engine(),
                    int(from_row),
                    int(from_column),
                    int(to_row),
                    int(to_column),
                )
            elif isinstance(room.game, GomokuGame):
                game = room.game
                requested_color = int(color) if int(color) in {1, 2} else game.human_color
                if game.turn == game.human_color:
                    if playful and requested_color != game.turn:
                        game.place_for_fun(
                            int(row), int(column), requested_color,
                            next_turn=game.human_color,
                        )
                    else:
                        game.place(int(row), int(column), game.human_color)
                elif playful and game.turn == game.bot_color and requested_color == game.bot_color:
                    game.place(int(row), int(column), game.bot_color)
                else:
                    raise ValueError("现在不是可拖拽落子的时机")
                board_event.update(
                    row=int(row),
                    column=int(column),
                    color=requested_color,
                    interaction="drag" if playful else "click",
                )
                if paired and not game.finished:
                    second_color = int(pair_color) if int(pair_color) in {1, 2} else (
                        game.bot_color if requested_color == game.human_color else game.human_color
                    )
                    if game.turn == second_color:
                        game.place(int(pair_row), int(pair_column), second_color)
                    elif playful:
                        game.place_for_fun(
                            int(pair_row), int(pair_column), second_color,
                            next_turn=game.human_color,
                        )
                    else:
                        raise ValueError("第二枚棋子无法落下")
                    follow_up_event = {
                        "actor": "bot" if second_color == game.bot_color else "human",
                        "row": int(pair_row),
                        "column": int(pair_column),
                        "color": second_color,
                        "interaction": "drag_pair",
                    }
            elif isinstance(room.game, TicTacToeGame):
                room.game.place(int(row), int(column), room.game.human_mark)
                board_event.update(
                    row=int(row),
                    column=int(column),
                    color=room.game.human_mark,
                )
            else:
                raise ValueError("当前游戏不支持棋盘落子")
            room.touch()
            finished = room.game.finished
        await self._emit("board_changed", room, board_event)
        if follow_up_event is not None:
            await self._emit("board_changed", room, follow_up_event)
        if finished:
            await self._finish_game(room)
            return
        if follow_up_event is None or (
            isinstance(room.game, GomokuGame) and room.game.turn == room.game.bot_color
        ):
            await self._bot_turn(room)

    async def player_dice_action(
        self, room: GameRoom, visitor_token: str, action: str
    ) -> None:
        """Apply one authoritative player action in Pig, then run the Bot turn."""
        normalized = str(action or "").strip().lower()
        if normalized not in {"roll", "hold"}:
            raise ValueError("骰子操作只能是继续掷或收手")
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if visitor.token != room.player_token:
                raise PermissionError("当前浏览器不在玩家席")
            if room.status != "active" or not isinstance(room.game, PigDiceGame):
                raise ValueError("当前没有正在进行的贪心骰子")
            game = room.game
            if game.turn != "human":
                raise ValueError("现在是 Bot 的回合")
            if normalized == "roll":
                game.roll("human")
            else:
                game.hold("human")
            payload = dict(game.history[-1])
            room.touch()
            finished = game.finished
            bot_turn = not finished and game.turn == "bot"
        await self._emit("dice_changed", room, payload)
        if finished:
            await self._finish_game(room)
        elif bot_turn:
            await self._bot_turn(room)

    async def player_blackjack_action(
        self, room: GameRoom, visitor_token: str, action: str
    ) -> None:
        """Apply one hit or stand for the player whose turn it is."""
        normalized = str(action or "").strip().lower()
        if normalized not in {"hit", "stand"}:
            raise ValueError("二十一点操作只能是要牌或停牌")
        dealer_ready = False
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if room.status != "active" or not isinstance(room.game, BlackjackGame):
                raise ValueError("当前没有正在进行的二十一点")
            game = room.game
            if game.finished or game.phase != "player_turns":
                raise ValueError("当前不是玩家要牌阶段")
            if visitor.token != room.multiplayer.current_token:
                raise PermissionError("还没轮到这位玩家")
            event = (
                game.hit(visitor.number)
                if normalized == "hit"
                else game.stand(visitor.number)
            )
            room.touch()
            if game.all_players_done():
                dealer_ready = True
                room.multiplayer.turn_deadline = 0.0
            else:
                dealer_ready = self._advance_blackjack_turn(room)
        await self._emit("blackjack_changed", room, event)
        if dealer_ready:
            await self._blackjack_dealer_turn(room)

    def _advance_blackjack_turn(
        self, room: GameRoom, *, now: float | None = None
    ) -> bool:
        """Move to the next unresolved hand and return whether all hands are done."""
        state = room.multiplayer
        game = room.game
        if (
            not state.enabled
            or not state.seats
            or not isinstance(game, BlackjackGame)
            or game.phase != "player_turns"
            or game.finished
        ):
            state.turn_deadline = 0.0
            return False
        current = time.time() if now is None else float(now)
        count = len(state.seats)
        start = state.current_turn_index % count
        for offset in range(count):
            index = (start + offset) % count
            seat = state.seats[index]
            visitor = room.visitors.get(seat.visitor_token)
            if visitor is None:
                continue
            hand = game.hands.get(visitor.number)
            if hand is None or hand.status != "playing":
                continue
            if visitor.connected and current - visitor.last_seen_at < 15:
                state.current_turn_index = index
                self._reset_turn_deadline(room, now=current)
                return game.all_players_done()
            game.stand(visitor.number)
            room.add_message(
                "system",
                f"{self._visitor_label(visitor)}已离线，当前手牌自动停牌。",
            )
            if game.all_players_done():
                state.turn_deadline = 0.0
                return True
        state.turn_deadline = 0.0
        return game.all_players_done()

    async def _blackjack_dealer_turn(self, room: GameRoom) -> None:
        """Reveal the dealer hole card, draw to 17, then settle all hands."""
        async with room.lock:
            game = room.game
            if (
                room.status != "active"
                or not isinstance(game, BlackjackGame)
                or game.finished
                or game.phase != "player_turns"
                or not game.all_players_done()
            ):
                return
            game.phase = "dealer_turn"
            game.reveal_dealer()
            room.touch()
        await self._emit(
            "blackjack_dealer_revealed",
            room,
            {
                "dealer_total": game.dealer_total,
                "dealer_soft": game.dealer_soft,
            },
        )
        finished = False
        while True:
            async with room.lock:
                game = room.game
                if (
                    room.status != "active"
                    or not isinstance(game, BlackjackGame)
                    or game.phase != "dealer_turn"
                    or game.finished
                ):
                    return
                if game.has_pending_hands() and game.dealer_must_hit:
                    card = game.draw_dealer()
                    room.touch()
                    event = {
                        "action": "dealer_hit",
                        "card": card.as_dict(),
                        "dealer_total": game.dealer_total,
                        "dealer_soft": game.dealer_soft,
                    }
                else:
                    game.settle()
                    room.touch()
                    event = {
                        "action": "settle",
                        "dealer_total": game.dealer_total,
                        "dealer_soft": game.dealer_soft,
                        "results": {
                            str(key): value for key, value in game.results.items()
                        },
                    }
                    finished = True
            await self._emit("blackjack_changed", room, event)
            if finished:
                break
            await asyncio.sleep(0.65)
        await self._finish_game(room)

    async def update_drawing(
        self, room: GameRoom, visitor_token: str, strokes: Any
    ) -> None:
        """Replace the authoritative drawing with one bounded stroke document."""
        normalized = self._normalize_strokes(strokes)
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if visitor.token != room.player_token:
                raise PermissionError("只有玩家席可以操作画布")
            if room.status != "active" or not isinstance(room.game, DrawGuessGame):
                raise ValueError("当前没有正在进行的你画我猜")
            if room.game.processing:
                raise ValueError("Bot 正在看图，暂时不能修改画布")
            room.game.replace_strokes(normalized)
            room.touch()
        await self._emit("drawing_changed", room, {"revision": room.game.revision})

    async def begin_draw_guess(
        self, room: GameRoom, visitor_token: str
    ) -> DrawGuessGame:
        """Reserve one visual guess without exposing the hidden target."""
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if visitor.token != room.player_token:
                raise PermissionError("只有玩家席可以让 Bot 猜图")
            if room.status != "active" or not isinstance(room.game, DrawGuessGame):
                raise ValueError("当前没有正在进行的你画我猜")
            game = room.game
            if game.processing:
                raise ValueError("Bot 已经在看这幅画了")
            if game.finished:
                raise ValueError("本局已经结束")
            if not game.strokes:
                raise ValueError("请先画几笔，再让 Bot 猜")
            if game.is_expired():
                game.timeout()
                raise ValueError("作画时间已经结束")
            game.processing = True
            room.touch()
            return game

    async def abort_draw_guess(self, room: GameRoom) -> None:
        async with room.lock:
            if isinstance(room.game, DrawGuessGame):
                room.game.processing = False

    async def complete_draw_guess(
        self, room: GameRoom, visitor_token: str, guess: str
    ) -> dict[str, Any]:
        """Record one Bot guess and finish the cooperative round when appropriate."""
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            if visitor.token != room.player_token:
                raise PermissionError("玩家席已经发生变化")
            if room.status != "active" or not isinstance(room.game, DrawGuessGame):
                raise ValueError("当前你画我猜已经结束")
            game = room.game
            if not game.processing:
                raise ValueError("当前没有待完成的 Bot 猜测")
            correct = game.matches(guess)
            item = game.record_guess(guess, correct=correct)
            game.processing = False
            room.touch()
            room.add_message(
                "bot",
                f"我猜是“{item['guess']}”。" + ("猜中了。" if correct else "好像不对。"),
                message_type="game",
            )
            finished = game.finished
        await self._emit("draw_guess_completed", room, dict(item))
        if finished:
            await self._finish_game(room)
        return dict(item)

    async def request_rematch(
        self,
        room: GameRoom,
        visitor_token: str,
        *,
        record_message: bool = True,
        request_text: str = "",
    ) -> None:
        """Put a finished room into a Bot-decided rematch request state."""
        self.require_game_enabled(room.game_type)
        async with room.lock:
            visitor = self._visitor(room, visitor_token)
            expected = (
                room.multiplayer.current_token
                if room.multiplayer.enabled
                else room.player_token
            )
            if visitor.token != expected:
                raise PermissionError("只有当前玩家能申请再来一局")
            if room.status != "finished":
                raise ValueError("当前还不能申请再来一局")
            room.status = "rematch_pending"
            room.touch()
            if record_message:
                room.add_message(
                    "user", "想再来一局。", visitor=visitor, message_type="control"
                )
        await self._emit(
            "rematch_requested",
            room,
            {
                "visitor_token": visitor_token,
                "request_text": str(request_text or "想再来一局。").strip()[:240],
            },
        )

    async def resolve_rematch(
        self,
        room: GameRoom,
        *,
        accepted: bool,
        message: str = "",
        difficulty: Difficulty | None = None,
    ) -> bool:
        """Apply a pending Bot decision and reject stale concurrent decisions."""
        async with room.lock:
            if room.status != "rematch_pending":
                return False
            if message:
                room.add_message("bot", message)
            if accepted:
                if difficulty is not None:
                    room.difficulty = difficulty
                room.touch()
                return True
        if not accepted:
            await self.destroy(room.room_id, "Bot 没有接受再来一局")
        return True

    async def restart_finished_game(
        self, room: GameRoom, *, difficulty: Difficulty
    ) -> None:
        """Start another game in the same room, preserving its player and score."""
        self.require_game_enabled(room.game_type)
        async with room.lock:
            if not room.player_token or room.player is None:
                raise ValueError("当前房间没有可以继续对局的玩家")
            if room.status not in {"finished", "rematch_pending"} or room.game is None:
                raise ValueError("当前对局尚未结束，不能直接再来一局")
            room.difficulty = difficulty
            generation_requested = (
                isinstance(room.game, TurtleSoupGame)
                and room.turtle_soup_mode == "bot_host"
            )
            if isinstance(room.game, TurtleSoupGame):
                room.game = TurtleSoupGame(
                    difficulty=difficulty,
                    max_hints=self.turtle_soup_max_hints,
                    content_level=self.turtle_soup_content_level,
                    mode=room.turtle_soup_mode,
                )
                side_label = ""
            elif isinstance(room.game, XiangqiGame):
                human_side = room.game.human_side
                room.game = await XiangqiGame.create(
                    self._require_xiangqi_engine(),
                    human_side=human_side,
                    difficulty=difficulty,
                )
                side_label = "红" if human_side == XIANGQI_RED else "黑"
            elif isinstance(room.game, GomokuGame):
                human_color = room.game.human_color
                room.game = GomokuGame(human_color=human_color, difficulty=difficulty)
                side_label = "黑" if human_color == BLACK else "白"
            elif isinstance(room.game, TicTacToeGame):
                human_mark = room.game.human_mark
                room.game = TicTacToeGame(human_mark=human_mark, difficulty=difficulty)
                side_label = "X" if human_mark == TICTACTOE_X else "O"
            elif isinstance(room.game, PigDiceGame):
                room.game = PigDiceGame(
                    difficulty=difficulty,
                    target_score=self.pig_dice_target_score,
                )
                side_label = ""
            elif isinstance(room.game, DrawGuessGame):
                room.game = DrawGuessGame(
                    difficulty=difficulty,
                    max_guesses=self.draw_guess_max_guesses,
                    duration_seconds=self.draw_guess_duration_seconds,
                )
                side_label = ""
            elif isinstance(room.game, BlackjackGame):
                room.game = BlackjackGame.deal(
                    difficulty=difficulty,
                    player_numbers=[
                        room.visitors[seat.visitor_token].number
                        for seat in room.multiplayer.seats
                        if seat.visitor_token in room.visitors
                    ],
                )
                room.multiplayer.current_turn_index = 0
                side_label = ""
            else:
                raise ValueError("当前游戏状态无法重新开始")
            room.status = "active"
            room.touch()
            if isinstance(room.game, TurtleSoupGame):
                room.add_message("system", "Bot 正在准备一道全新的海龟汤。")
                if room.game.mode == "player_host":
                    room.messages[-1]["content"] = (
                        "新一轮玩家出题开始，请当前玩家提供第一条公开线索。"
                    )
            elif isinstance(room.game, PigDiceGame):
                first = "玩家" if room.game.turn == "human" else "Bot"
                room.add_message(
                    "system", f"新一局贪心骰子开始，由{first}先掷，目标 50 分。"
                )
            elif isinstance(room.game, DrawGuessGame):
                room.add_message(
                    "system",
                    f"新一轮你画我猜开始，可让 Bot 猜 {room.game.max_guesses} 次。",
                )
            elif isinstance(room.game, BlackjackGame):
                room.add_message(
                    "system", "新一轮二十一点开始，Bot 是庄家，请当前玩家先行动。"
                )
            else:
                room.add_message(
                    "system",
                    f"新对局开始，玩家继续执{side_label}，Bot 使用{self._difficulty_label(difficulty)}棋力。",
                )
            self._reset_turn_deadline(room)
        if generation_requested:
            await self._emit("soup_generation_requested", room, {"rematch": True})
            return
        if isinstance(room.game, BlackjackGame):
            async with room.lock:
                if not room.game.finished:
                    self._advance_blackjack_turn(room)
                self._reset_turn_deadline(room)
            if room.game.finished:
                await self._finish_game(room)
                return
            if room.game.all_players_done():
                await self._blackjack_dealer_turn(room)
                return
        await self._emit("game_started", room, {"rematch": True})
        if self._is_bot_turn(room):
            await self._bot_turn(room)

    async def undo(self, room: GameRoom) -> int:
        """Undo the latest complete player round after a QQ-side decision."""
        async with room.lock:
            if room.status != "active" or room.game is None:
                raise ValueError("当前没有可以悔棋的对局")
            if isinstance(room.game, (TurtleSoupGame, PigDiceGame, DrawGuessGame)):
                raise ValueError("当前游戏不支持悔棋")
            if isinstance(room.game, XiangqiGame):
                removed = await room.game.undo_round(self._require_xiangqi_engine())
            else:
                removed = room.game.undo_round()
            room.touch()
            room.add_message("system", "Bot 同意了悔棋。")
            return removed

    async def resign(self, room: GameRoom, *, visitor_token: str = "") -> None:
        """Finish a solo game as a Bot win, or surrender one Blackjack hand."""
        dealer_ready = False
        blackjack_continues = False
        async with room.lock:
            if room.status != "active" or room.game is None:
                raise ValueError("当前没有正在进行的对局")
            if isinstance(room.game, BlackjackGame):
                game = room.game
                if game.finished or game.phase != "player_turns":
                    raise ValueError("当前不是玩家要牌阶段")
                if (
                    not visitor_token
                    or visitor_token != room.multiplayer.current_token
                ):
                    raise PermissionError("还没轮到这位玩家投降")
                visitor = self._visitor(room, visitor_token)
                game.surrender(visitor.number)
                room.add_message(
                    "system",
                    f"{self._visitor_label(visitor)}选择投降，这一手记为输。",
                )
                room.touch()
                if game.all_players_done():
                    dealer_ready = True
                    room.multiplayer.turn_deadline = 0.0
                else:
                    self._advance_blackjack_turn(room)
                    blackjack_continues = True
            elif isinstance(room.game, TurtleSoupGame):
                room.game.give_up()
            elif isinstance(room.game, XiangqiGame):
                room.game.winner = room.game.bot_side
            elif isinstance(room.game, GomokuGame):
                room.game.winner = room.game.bot_color
            elif isinstance(room.game, TicTacToeGame):
                room.game.winner = room.game.bot_mark
            elif isinstance(room.game, PigDiceGame):
                room.game.resign_human()
            elif isinstance(room.game, DrawGuessGame):
                room.game.timeout()
            else:
                raise ValueError("当前游戏不能认输")
            room.touch()
        if blackjack_continues:
            return
        if dealer_ready:
            await self._blackjack_dealer_turn(room)
            return
        await self._finish_game(room)

    async def remove_player(
        self,
        room: GameRoom,
        visitor_number: int = 0,
        *,
        reason: str = "玩家已被移到观众席",
    ) -> None:
        """Clear one seat, preserving multiplayer games while seats remain."""
        dealer_ready = False
        async with room.lock:
            if room.multiplayer.enabled:
                target = (
                    self._visitor_by_number(room, visitor_number)
                    if visitor_number
                    else room.player
                )
                if target is None:
                    raise ValueError("当前没有玩家可以移到观众席")
                self._remove_multiplayer_seat(room, target.token)
                dealer_ready = bool(
                    isinstance(room.game, BlackjackGame)
                    and room.status == "active"
                    and not room.game.finished
                    and room.game.phase == "player_turns"
                    and room.game.all_players_done()
                )
                if room.multiplayer.seats:
                    room.player_empty_since = None
                    self._sync_primary_player(room)
                    self._reset_turn_deadline(room)
                else:
                    self._clear_primary_player(room)
                    room.player_empty_since = time.time()
                    room.game = None
                    room.status = "waiting"
            else:
                self._clear_primary_player(room)
                room.player_seat_locked = room.admin_room
                room.player_empty_since = time.time()
                room.game = None
                room.status = "waiting"
            room.touch()
            room.add_message("system", reason)
        if dealer_ready:
            await self._blackjack_dealer_turn(room)

    async def kick_visitor(self, room: GameRoom, visitor_number: int) -> None:
        """Invalidate one browser identity and clear its seat if necessary."""
        dealer_ready = False
        async with room.lock:
            visitor = self._visitor_by_number(room, visitor_number)
            was_player = (
                room.multiplayer.seat_for_token(visitor.token) is not None
                if room.multiplayer.enabled
                else visitor.token == room.player_token
            )
            if room.multiplayer.enabled and was_player:
                self._remove_multiplayer_seat(room, visitor.token)
                dealer_ready = bool(
                    isinstance(room.game, BlackjackGame)
                    and room.status == "active"
                    and not room.game.finished
                    and room.game.phase == "player_turns"
                    and room.game.all_players_done()
                )
            room.visitors.pop(visitor.token, None)
            if was_player:
                if room.multiplayer.enabled and room.multiplayer.seats:
                    self._sync_primary_player(room)
                    room.player_empty_since = None
                    self._reset_turn_deadline(room)
                else:
                    self._clear_primary_player(room)
                    room.player_empty_since = time.time()
                    room.game = None
                    room.status = "waiting"
            room.touch()
            room.add_message("system", f"{visitor.number} 号已被移出房间。")
        if dealer_ready:
            await self._blackjack_dealer_turn(room)

    async def pause(self, room: GameRoom) -> None:
        """Pause an active game without changing its board."""
        async with room.lock:
            if room.status != "active":
                raise ValueError("当前对局不能暂停")
            if isinstance(room.game, DrawGuessGame):
                room.game.pause()
            room.status = "paused"
            self._reset_turn_deadline(room)
            room.touch()
            room.add_message("system", "对局已暂停。")

    async def resume(self, room: GameRoom) -> None:
        """Resume a paused game."""
        async with room.lock:
            if room.status != "paused" or room.game is None:
                raise ValueError("当前没有已暂停的对局")
            if isinstance(room.game, DrawGuessGame):
                room.game.resume()
            room.status = "active"
            room.touch()
            self._reset_turn_deadline(room)
            room.add_message("system", "对局继续。")
            blackjack_dealer = bool(
                isinstance(room.game, BlackjackGame)
                and room.game.phase == "dealer_turn"
                and not room.game.finished
            )
            bot_turn = not isinstance(
                room.game, (TurtleSoupGame, DrawGuessGame)
            ) and self._game_is_bot_turn(room.game)
        if blackjack_dealer:
            await self._blackjack_dealer_turn(room)
            return
        if bot_turn:
            await self._bot_turn(room)

    async def switch_game(
        self,
        room: GameRoom,
        game_type: GameType,
        *,
        force: bool = False,
    ) -> bool:
        """Switch one room's game while preserving access, seats, and scores."""
        if game_type not in SUPPORTED_GAMES:
            raise ValueError("不支持的游戏类型")
        self.require_game_enabled(game_type)
        if game_type == "xiangqi":
            await self._require_xiangqi_engine().ensure_ready()
        async with room.lock:
            if room.game_type == game_type:
                return False
            if room.status in {"active", "paused"} and not force:
                raise ValueError("当前对局尚未结束，需要明确放弃本局后才能切换游戏")
            previous = room.game_type
            room.game_type = game_type
            self._configure_multiplayer(room)
            room.game = None
            room.status = "setup" if room.player_token else "waiting"
            room.player_empty_since = None if room.player_token else time.time()
            room.touch()
            room.add_message(
                "system",
                f"游戏已从{self._game_label(previous)}切换为{self._game_label(game_type)}。",
            )
        await self._emit(
            "game_switched", room, {"from": previous, "to": game_type, "forced": force}
        )
        return True

    def game_enabled(self, game_type: GameType) -> bool:
        """Return whether new rounds of one supported game may start."""
        return bool(self.enabled_games.get(game_type, False))

    def require_game_enabled(self, game_type: GameType) -> None:
        """Reject new rooms, switches and rematches for an administratively closed game."""
        if not self.game_enabled(game_type):
            raise PermissionError(f"管理员暂未开放{self._game_label(game_type)}")

    async def switch_turtle_soup_mode(
        self,
        room: GameRoom,
        mode: TurtleSoupMode,
        *,
        force: bool = False,
    ) -> bool:
        """Change only the turtle-soup variant while preserving room seats."""
        if mode not in {"bot_host", "player_host"}:
            raise ValueError("不支持的海龟汤玩法")
        async with room.lock:
            if room.game_type != "turtle_soup":
                raise ValueError("当前房间不是海龟汤")
            if room.turtle_soup_mode == mode:
                return False
            if room.status in {"active", "paused"} and not force:
                raise ValueError("当前海龟汤尚未结束，需要明确放弃后才能切换玩法")
            room.turtle_soup_mode = mode
            room.game = None
            room.status = "setup" if room.player_token else "waiting"
            room.touch()
            room.add_message(
                "system",
                "海龟汤玩法已切换为"
                + (
                    "Bot 出题、玩家猜。" if mode == "bot_host" else "玩家出题、Bot 猜。"
                ),
            )
            self._reset_turn_deadline(room)
        await self._emit("game_switched", room, {"soup_mode": mode})
        return True

    async def complete_turtle_soup_generation(
        self,
        room: GameRoom,
        game: TurtleSoupGame,
        puzzle: SoupPuzzle,
    ) -> bool:
        """Publish one immutable puzzle if the room still expects it."""
        async with room.lock:
            if room.game is not game or room.status not in {"active", "paused"}:
                return False
            game.set_puzzle(puzzle)
            room.turtle_soup_recent_signatures.append(puzzle.signature)
            del room.turtle_soup_recent_signatures[:-8]
            room.touch()
            room.add_message("system", f"海龟汤《{puzzle.title}》已经准备好。")
            self._reset_turn_deadline(room)
        await self._emit("game_started", room, {"turtle_soup": True})
        return True

    async def begin_turtle_soup_interaction(
        self,
        room: GameRoom,
        text: str,
        *,
        source: str,
        visitor_token: str = "",
        actor_qq: str = "",
        limit: int,
    ) -> tuple[TurtleSoupGame, str]:
        """Reserve the single judge slot without holding the lock during LLM work."""
        cleaned = clean_player_text(text, limit=limit)
        async with room.lock:
            game = self._turtle_soup_game(room)
            player_number = self._require_turtle_soup_player(
                room,
                source=source,
                visitor_token=visitor_token,
                actor_qq=actor_qq,
            )
            if room.status == "paused":
                raise ValueError("当前海龟汤已经暂停")
            if room.status != "active":
                raise ValueError("当前没有正在进行的海龟汤")
            game.begin_processing()
            game.processing_player_number = player_number
            room.multiplayer.turn_deadline = 0.0
            room.touch()
            return game, cleaned

    async def cancel_turtle_soup_interaction(
        self, room: GameRoom, game: TurtleSoupGame, reason: str
    ) -> None:
        async with room.lock:
            if room.game is game:
                game.cancel_processing(reason)
                self._reset_turn_deadline(room)

    async def resolve_turtle_soup_question(
        self,
        room: GameRoom,
        game: TurtleSoupGame,
        question: str,
        verdict: SoupVerdict,
        *,
        source: Literal["web", "qq"],
        matched_facts: set[int],
    ) -> bool:
        async with room.lock:
            if room.game is not game or room.status != "active":
                return False
            newly_discovered = game.record_question(
                question,
                verdict,
                source=source,
                matched_facts=matched_facts,
                player_number=game.processing_player_number,
            )
            room.touch()
            self._advance_multiplayer_turn(room)
        await self._emit(
            "soup_question_answered",
            room,
            {
                "source": source,
                "verdict": verdict,
                "new_facts": len(newly_discovered),
            },
        )
        return True

    async def resolve_turtle_soup_answer(
        self,
        room: GameRoom,
        game: TurtleSoupGame,
        answer: str,
        *,
        solved: bool,
        source: Literal["web", "qq"],
        matched_facts: set[int],
    ) -> bool:
        async with room.lock:
            if room.game is not game or room.status != "active":
                return False
            newly_discovered = game.record_answer(
                answer,
                solved=solved,
                source=source,
                matched_facts=matched_facts,
                player_number=game.processing_player_number,
            )
            room.touch()
            self._advance_multiplayer_turn(room)
        if solved:
            await self._finish_game(room)
        else:
            await self._emit(
                "soup_answer_attempted",
                room,
                {"source": source, "new_facts": len(newly_discovered)},
            )
        return True

    async def request_turtle_soup_hint(
        self,
        room: GameRoom,
        *,
        source: Literal["web", "qq"],
        visitor_token: str = "",
        actor_qq: str = "",
    ) -> str:
        async with room.lock:
            game = self._turtle_soup_game(room)
            player_number = self._require_turtle_soup_player(
                room,
                source=source,
                visitor_token=visitor_token,
                actor_qq=actor_qq,
            )
            if room.status != "active":
                raise ValueError("当前不能申请提示")
            if game.mode != "bot_host":
                raise ValueError("玩家出题模式不提供 Bot 预设提示")
            hint = game.reveal_hint(source=source, player_number=player_number)
            room.touch()
            self._advance_multiplayer_turn(room)
        await self._emit(
            "soup_hint_revealed",
            room,
            {
                "source": source,
                "hint": hint,
                "visitor_token": visitor_token,
            },
        )
        return hint

    async def resolve_reverse_turtle_soup_turn(
        self,
        room: GameRoom,
        game: TurtleSoupGame,
        player_text: str,
        *,
        bot_action: Literal["question", "guess"],
        bot_text: str,
        source: Literal["web", "qq"],
    ) -> bool:
        """Publish one public Bot question or guess in player-hosted mode."""
        async with room.lock:
            if room.game is not game or room.status != "active":
                return False
            player_number = game.processing_player_number
            game.record_reverse_turn(
                player_text,
                bot_action=bot_action,
                bot_text=bot_text,
                source=source,
                player_number=player_number,
            )
            room.add_message("bot", bot_text)
            room.touch()
            self._advance_multiplayer_turn(room)
        await self._emit(
            "soup_reverse_turn",
            room,
            {"source": source, "bot_action": bot_action},
        )
        return True

    async def confirm_reverse_turtle_soup_guess(
        self,
        room: GameRoom,
        *,
        source: Literal["web", "qq"],
        visitor_token: str = "",
        actor_qq: str = "",
    ) -> None:
        """Let the current player authoritatively mark the Bot's guess correct."""
        async with room.lock:
            game = self._turtle_soup_game(room)
            self._require_turtle_soup_player(
                room,
                source=source,
                visitor_token=visitor_token,
                actor_qq=actor_qq,
            )
            if room.status != "active":
                raise ValueError("当前不能判定 Bot 的猜测")
            game.confirm_bot_guess(correct=True)
            room.touch()
        await self._finish_game(room)

    async def request_seat_swap(
        self,
        room: GameRoom,
        visitor_token: str,
        target_number: int,
        *,
        now: float | None = None,
    ) -> str:
        """Create a rate-limited spectator request for one occupied seat."""
        current = time.time() if now is None else float(now)
        async with room.lock:
            state = room.multiplayer
            if not state.enabled:
                raise ValueError("当前游戏不支持多人席位交换")
            requester = self._visitor(room, visitor_token)
            self._purge_swap_requests(room, current)
            if state.seat_for_token(requester.token) is not None:
                raise ValueError("玩家席内不能申请交换其他玩家")
            target = self._visitor_by_number(room, target_number)
            if state.seat_for_token(target.token) is None:
                raise ValueError("目标访客当前不在玩家席")
            cooldown_until = (
                state.last_swap_request_at.get(requester.token, 0)
                + state.swap_cooldown_seconds
            )
            if state.swap_cooldown_seconds and current < cooldown_until:
                remaining = max(1, int(cooldown_until - current + 0.999))
                raise ValueError(f"请等待 {remaining} 秒后再发送交换申请")
            if any(
                item.requester_token == requester.token
                for item in state.swap_requests.values()
            ):
                raise ValueError("你已经有一条等待处理的交换申请")
            request_id = secrets.token_urlsafe(10)
            state.swap_requests[request_id] = SeatSwapRequest(
                request_id=request_id,
                requester_token=requester.token,
                target_token=target.token,
                created_at=current,
                expires_at=current + state.swap_request_expiry_seconds,
            )
            state.last_swap_request_at[requester.token] = current
        return request_id

    async def resolve_seat_swap(
        self,
        room: GameRoom,
        visitor_token: str,
        request_id: str,
        *,
        accepted: bool,
        now: float | None = None,
    ) -> bool:
        """Accept or decline one request as its target player."""
        current = time.time() if now is None else float(now)
        async with room.lock:
            state = room.multiplayer
            visitor = self._visitor(room, visitor_token)
            self._purge_swap_requests(room, current)
            swap = state.swap_requests.get(str(request_id or ""))
            if swap is None:
                raise ValueError("交换申请不存在或已经失效")
            if swap.target_token != visitor.token:
                raise PermissionError("只有被申请的玩家可以处理这条申请")
            requester = room.visitors.get(swap.requester_token)
            state.swap_requests.pop(swap.request_id, None)
            if not accepted:
                return False
            if requester is None or state.seat_for_token(requester.token) is not None:
                raise ValueError("申请者已经离开或身份已经变化")
            if not room.admin_room and not requester.identity_confirmed:
                raise PermissionError("申请者需要先在 QQ 中绑定页面令牌")
            target_index = next(
                (
                    index
                    for index, seat in enumerate(state.seats)
                    if seat.visitor_token == swap.target_token
                ),
                -1,
            )
            if target_index < 0:
                raise ValueError("目标玩家已经不在玩家席")
            state.seats[target_index] = PlayerSeat(
                visitor_token=requester.token,
                qq=requester.qq if requester.identity_confirmed else "",
                display_name=requester.display_name,
                identity_confirmed=requester.identity_confirmed,
            )
            if requester.identity_confirmed:
                room.confirmed_participant_qqs.add(requester.qq)
                room.participant_names[requester.qq] = requester.display_name
            state.swap_requests = {
                key: item
                for key, item in state.swap_requests.items()
                if requester.token not in {item.requester_token, item.target_token}
                and visitor.token not in {item.requester_token, item.target_token}
            }
            self._sync_primary_player(room)
            self._reset_turn_deadline(room, now=current)
            room.add_message(
                "system",
                f"{self._visitor_label(requester)}与 {self._visitor_label(visitor)}完成席位交换。",
            )
            room.touch()
        await self._emit("seats_changed", room, {"swapped": True})
        return True

    async def destroy(self, room_id: str, reason: str) -> GameRoom | None:
        """Destroy a room and release its quota exactly once."""
        async with self._lock:
            room = self.rooms.pop(str(room_id or ""), None)
            if room is None:
                return None
            self._access_index.pop(room.access_token, None)
            room.status = "closed"
            room.close_reason = str(reason or "房间已结束")[:200]
            self._closed_access[room.access_token] = (
                room.close_reason,
                time.time() + self.CLOSED_ACCESS_TTL_SECONDS,
            )
            self._purge_closed_access()
        await self._emit("room_destroyed", room, {"reason": room.close_reason})
        return room

    async def sweep_expired(self, *, now: float | None = None) -> list[str]:
        """Destroy rooms that exceed the configured empty or idle timeout."""
        current = time.time() if now is None else float(now)
        expired: list[tuple[str, str]] = []
        for room in list(self.rooms.values()):
            await self._tick_multiplayer_room(room, current)
            if (
                room.status == "active"
                and isinstance(room.game, DrawGuessGame)
                and not room.game.processing
                and room.game.is_expired(current)
            ):
                async with room.lock:
                    room.game.timeout()
                    room.touch()
                await self._finish_game(room)
            player = room.player
            if room.status == "finished" and player is not None:
                seated_visitors = (
                    [
                        room.visitors[seat.visitor_token]
                        for seat in room.multiplayer.seats
                        if seat.visitor_token in room.visitors
                    ]
                    if room.multiplayer.enabled
                    else [player]
                )
                all_departed = bool(seated_visitors) and all(
                    (
                        visitor.left_at is not None
                        and current - visitor.left_at
                        >= self.FINISHED_PLAYER_LEAVE_GRACE_SECONDS
                    )
                    or current - visitor.last_seen_at
                    >= self.FINISHED_PLAYER_HEARTBEAT_TIMEOUT_SECONDS
                    for visitor in seated_visitors
                )
                if all_departed:
                    expired.append(
                        (room.room_id, "本局结束后玩家已离开，房间已自动销毁")
                    )
                    continue
            if isinstance(room.game, TurtleSoupGame) and room.game.phase == "preparing":
                continue
            if (
                self.empty_player_timeout
                and room.player_empty_since is not None
                and current - room.player_empty_since >= self.empty_player_timeout
            ):
                expired.append((room.room_id, "玩家席长时间无人，房间已自动销毁"))
                continue
            if (
                self.idle_timeout
                and current - room.last_activity_at >= self.idle_timeout
            ):
                expired.append((room.room_id, "房间长时间无操作，已自动销毁"))
        for room_id, reason in expired:
            await self.destroy(room_id, reason)
        return [room_id for room_id, _reason in expired]

    async def close_all(self, reason: str = "插件已重载") -> None:
        """Destroy every in-memory room without persistence."""
        for room_id in list(self.rooms):
            await self.destroy(room_id, reason)

    async def _bot_turn(self, room: GameRoom) -> None:
        await asyncio.sleep(
            {"easy": 0.55, "normal": 0.85, "hard": 1.05}[room.difficulty]
        )
        if isinstance(room.game, PigDiceGame):
            await self._pig_dice_bot_turn(room)
            return
        board_event: dict[str, object] = {"actor": "bot"}
        async with room.lock:
            if room.status != "active" or room.game is None or room.game.finished:
                return
            game = room.game
            if isinstance(game, (TurtleSoupGame, DrawGuessGame)):
                return
            if isinstance(game, XiangqiGame):
                try:
                    await game.place_bot(self._require_xiangqi_engine())
                except Exception:
                    room.status = "paused"
                    room.add_message(
                        "system",
                        "象棋引擎暂时不可用，对局已暂停。恢复引擎后可在 QQ 或管理台继续。",
                    )
                    raise
            elif isinstance(game, GomokuGame):
                move = await asyncio.to_thread(game.choose_bot_move)
                game.place(move[0], move[1], game.bot_color)
                board_event.update(
                    row=move[0], column=move[1], color=game.bot_color
                )
            else:
                move = game.choose_bot_move()
                game.place(move[0], move[1], game.bot_mark)
                board_event.update(row=move[0], column=move[1], color=game.bot_mark)
            finished = game.finished
        await self._emit("board_changed", room, board_event)
        if finished:
            await self._finish_game(room)

    async def _pig_dice_bot_turn(self, room: GameRoom) -> None:
        """Play a visible multi-roll Bot turn without invoking the language model."""
        while True:
            async with room.lock:
                if (
                    room.status != "active"
                    or not isinstance(room.game, PigDiceGame)
                    or room.game.finished
                    or room.game.turn != "bot"
                ):
                    return
                game = room.game
                if game.bot_should_hold():
                    game.hold("bot")
                else:
                    game.roll("bot")
                payload = dict(game.history[-1])
                room.touch()
                finished = game.finished
                bot_done = finished or game.turn != "bot"
            await self._emit("dice_changed", room, payload)
            if finished:
                await self._finish_game(room)
                return
            if bot_done:
                return
            await asyncio.sleep(0.7)

    async def _finish_game(self, room: GameRoom) -> None:
        async with room.lock:
            if room.game is None or not room.game.finished or room.status == "finished":
                return
            room.status = "finished"
            room.completed_games += 1
            if isinstance(room.game, TurtleSoupGame):
                room.turtle_soup_stats.questions += room.game.question_count
                room.turtle_soup_stats.hints += room.game.hints_used
                room.turtle_soup_stats.answer_attempts += room.game.answer_attempts
                if room.game.winner == "human":
                    room.human_wins += 1
                    result = "human_win"
                else:
                    room.bot_wins += 1
                    result = "bot_win"
            elif isinstance(room.game, DrawGuessGame):
                if room.game.solved:
                    room.human_wins += 1
                    result = "cooperative_success"
                else:
                    room.bot_wins += 1
                    result = "cooperative_unsolved"
            elif isinstance(room.game, BlackjackGame):
                results = list(room.game.results.values())
                human = sum(
                    1 for value in results if value in {"win", "blackjack_win"}
                )
                bot = sum(1 for value in results if value == "loss")
                pushes = sum(1 for value in results if value == "push")
                room.human_wins += human
                room.bot_wins += bot
                room.draws += pushes
                if human and not bot:
                    result = "human_win"
                elif bot and not human:
                    result = "bot_win"
                elif human and bot:
                    result = "mixed"
                else:
                    result = "draw"
            elif getattr(room.game, "draw", False):
                room.draws += 1
                result = "draw"
            elif self._game_human_won(room.game):
                room.human_wins += 1
                result = "human_win"
            else:
                room.bot_wins += 1
                result = "bot_win"
            room.touch()
        await self._emit("game_finished", room, {"result": result})

    async def _emit(self, event: str, room: GameRoom, payload: dict[str, Any]) -> None:
        if self.event_callback is not None:
            await self.event_callback(event, room, payload)

    async def _tick_multiplayer_room(self, room: GameRoom, now: float) -> None:
        """Expire swap requests and rotate an overdue active turn."""
        dealer_ready = False
        async with room.lock:
            state = room.multiplayer
            if not state.enabled:
                return
            self._purge_swap_requests(room, now)
            game = room.game
            if isinstance(game, BlackjackGame):
                dealer_ready = self._tick_blackjack_locked(room, now)
            else:
                turn_active = bool(
                    room.status == "active"
                    and isinstance(game, TurtleSoupGame)
                    and game.phase == "ready"
                    and not game.processing
                    and state.seats
                    and state.turn_timeout_seconds
                )
                if not turn_active:
                    state.turn_deadline = 0.0
                    return
                if not state.turn_deadline:
                    self._reset_turn_deadline(room, now=now)
                    return
                if now < state.turn_deadline:
                    return
                previous = room.visitors.get(state.current_token)
                self._advance_multiplayer_turn(room, now=now)
                current = room.visitors.get(state.current_token)
                if previous and current and previous.token != current.token:
                    room.add_message(
                        "system",
                        f"{self._visitor_label(previous)}回合超时，已轮到 {self._visitor_label(current)}。",
                    )
        if dealer_ready:
            await self._blackjack_dealer_turn(room)

    def _tick_blackjack_locked(self, room: GameRoom, now: float) -> bool:
        """Rotate or auto-stand an overdue Blackjack hand; return dealer-ready."""
        state = room.multiplayer
        game = room.game
        if (
            room.status != "active"
            or not isinstance(game, BlackjackGame)
            or game.finished
            or game.phase != "player_turns"
        ):
            state.turn_deadline = 0.0
            return False
        if game.all_players_done():
            state.turn_deadline = 0.0
            return True
        previous = room.visitors.get(state.current_token)
        if previous is not None:
            hand = game.hands.get(previous.number)
            if hand is not None and hand.status == "playing":
                offline = not (
                    previous.connected and now - previous.last_seen_at < 15
                )
                if offline:
                    game.stand(previous.number)
                    room.add_message(
                        "system",
                        f"{self._visitor_label(previous)}已离线，当前手牌自动停牌。",
                    )
                    if game.all_players_done():
                        state.turn_deadline = 0.0
                        return True
                    return self._advance_blackjack_turn(room, now=now)
        if not state.seats or not state.turn_timeout_seconds:
            state.turn_deadline = 0.0
            return False
        if not state.turn_deadline:
            self._reset_turn_deadline(room, now=now)
            return False
        if now < state.turn_deadline:
            return False
        if previous is not None:
            hand = game.hands.get(previous.number)
            if hand is not None and hand.status == "playing":
                game.stand(previous.number)
                room.add_message(
                    "system",
                    f"{self._visitor_label(previous)}回合超时，已自动停牌。",
                )
        if game.all_players_done():
            state.turn_deadline = 0.0
            return True
        return self._advance_blackjack_turn(room, now=now)

    def _configure_multiplayer(self, room: GameRoom) -> None:
        """Apply the current game's seat policy without losing its primary player."""
        if room.game_type in {"turtle_soup", "blackjack"}:
            capacity = (
                self.turtle_soup_max_players
                if room.game_type == "turtle_soup"
                else self.blackjack_max_players
            )
            if room.multiplayer.enabled:
                room.multiplayer.capacity = capacity
                room.multiplayer.turn_timeout_seconds = self.multiplayer_turn_timeout
                room.multiplayer.swap_cooldown_seconds = self.swap_request_cooldown
                room.multiplayer.swap_request_expiry_seconds = self.swap_request_expiry
                return
            seats = (
                [
                    PlayerSeat(
                        visitor_token=room.player_token,
                        qq=room.player_qq,
                        display_name=(
                            room.visitors.get(room.player_token).display_name
                            if room.visitors.get(room.player_token)
                            else ""
                        ),
                        identity_confirmed=room.player_identity_confirmed,
                    )
                ]
                if room.player_token
                else []
            )
            room.multiplayer = MultiplayerState(
                enabled=True,
                capacity=capacity,
                turn_timeout_seconds=self.multiplayer_turn_timeout,
                swap_cooldown_seconds=self.swap_request_cooldown,
                swap_request_expiry_seconds=self.swap_request_expiry,
                seats=seats,
            )
            self._sync_primary_player(room)
            return
        if room.multiplayer.enabled:
            self._sync_primary_player(room)
        room.multiplayer = MultiplayerState()

    @staticmethod
    def _visitor_label(visitor: Visitor) -> str:
        return (
            f"{visitor.display_name}（{visitor.number}号）"
            if visitor.display_name
            else f"{visitor.number}号"
        )

    @staticmethod
    def _clear_primary_player(room: GameRoom) -> None:
        room.player_token = ""
        room.player_qq = ""
        room.player_identity_confirmed = False

    def _sync_primary_player(self, room: GameRoom) -> None:
        if not room.multiplayer.enabled or not room.multiplayer.seats:
            self._clear_primary_player(room)
            return
        seat = room.multiplayer.seats[0]
        room.player_token = seat.visitor_token
        room.player_qq = seat.qq
        room.player_identity_confirmed = seat.identity_confirmed

    def _remove_multiplayer_seat(self, room: GameRoom, visitor_token: str) -> None:
        state = room.multiplayer
        old_index = next(
            (
                index
                for index, seat in enumerate(state.seats)
                if seat.visitor_token == visitor_token
            ),
            -1,
        )
        if old_index < 0:
            raise ValueError("该访客当前不在玩家席")
        was_current = old_index == state.current_turn_index
        departing = room.visitors.get(visitor_token)
        if (
            departing is not None
            and isinstance(room.game, BlackjackGame)
            and not room.game.finished
        ):
            hand = room.game.hands.get(departing.number)
            if (
                hand is not None
                and hand.status in {"playing", "stand", "blackjack"}
                and not hand.result
            ):
                room.game.surrender(departing.number)
        state.seats.pop(old_index)
        if state.seats:
            if old_index < state.current_turn_index:
                state.current_turn_index -= 1
            elif was_current:
                state.current_turn_index %= len(state.seats)
            else:
                state.current_turn_index %= len(state.seats)
        else:
            state.current_turn_index = 0
            state.turn_deadline = 0.0
        if (
            state.seats
            and isinstance(room.game, BlackjackGame)
            and room.status == "active"
            and room.game.phase == "player_turns"
            and not room.game.finished
        ):
            self._advance_blackjack_turn(room)
        state.swap_requests = {
            key: item
            for key, item in state.swap_requests.items()
            if visitor_token not in {item.requester_token, item.target_token}
        }

    def _advance_multiplayer_turn(
        self, room: GameRoom, *, now: float | None = None
    ) -> None:
        state = room.multiplayer
        if not state.enabled or not state.seats:
            return
        current = time.time() if now is None else float(now)
        count = len(state.seats)
        start = state.current_turn_index % count
        chosen = start
        for offset in range(1, count + 1):
            index = (start + offset) % count
            visitor = room.visitors.get(state.seats[index].visitor_token)
            if visitor and visitor.connected and current - visitor.last_seen_at < 15:
                chosen = index
                break
        state.current_turn_index = chosen
        self._reset_turn_deadline(room, now=current)

    def _reset_turn_deadline(self, room: GameRoom, *, now: float | None = None) -> None:
        state = room.multiplayer
        game = room.game
        if not state.enabled:
            return
        active = bool(
            state.turn_timeout_seconds
            and state.seats
            and room.status == "active"
            and (
                (
                    isinstance(game, TurtleSoupGame)
                    and game.phase == "ready"
                    and not game.processing
                )
                or (
                    isinstance(game, BlackjackGame)
                    and game.phase == "player_turns"
                    and not game.finished
                    and not game.all_players_done()
                )
            )
        )
        state.turn_deadline = (
            (time.time() if now is None else float(now)) + state.turn_timeout_seconds
            if active
            else 0.0
        )

    @staticmethod
    def _purge_swap_requests(room: GameRoom, now: float) -> None:
        state = room.multiplayer
        state.swap_requests = {
            key: item
            for key, item in state.swap_requests.items()
            if item.expires_at > now
            and item.requester_token in room.visitors
            and item.target_token in room.visitors
        }

    def _purge_closed_access(self) -> None:
        now = time.time()
        expired = [
            token
            for token, (_reason, expires_at) in self._closed_access.items()
            if expires_at <= now
        ]
        for token in expired:
            self._closed_access.pop(token, None)
        overflow = len(self._closed_access) - self.MAX_CLOSED_ACCESS_RECORDS
        if overflow > 0:
            oldest = sorted(
                self._closed_access, key=lambda token: self._closed_access[token][1]
            )[:overflow]
            for token in oldest:
                self._closed_access.pop(token, None)

    @staticmethod
    def _visitor(room: GameRoom, token: str) -> Visitor:
        visitor = room.visitors.get(str(token or ""))
        if visitor is None:
            raise PermissionError("访客身份无效，请重新打开房间")
        return visitor

    @staticmethod
    def _visitor_by_number(room: GameRoom, number: int) -> Visitor:
        visitor = next(
            (item for item in room.visitors.values() if item.number == int(number)),
            None,
        )
        if visitor is None:
            raise ValueError(f"房间内没有 {number} 号访客")
        return visitor

    @staticmethod
    def _normalize_strokes(value: Any) -> list[dict[str, Any]]:
        from .draw_guess import normalize_strokes
        return normalize_strokes(value)

    def _confirm_visitor_identity(
        self,
        room: GameRoom,
        visitor: Visitor,
        qq: str,
        display_name: str,
        *,
        allow_trusted_enrollment: bool,
    ) -> None:
        visitor.qq = str(qq or "").strip()
        visitor.display_name = str(display_name or "").strip()[:40]
        visitor.identity_confirmed = True
        visitor.binding_token = ""
        visitor.binding_expires_at = 0.0
        visitor.trusted_browser_enrollment_pending = allow_trusted_enrollment
        seat = room.multiplayer.seat_for_token(visitor.token)
        if seat is not None:
            seat.qq = visitor.qq
            seat.display_name = visitor.display_name
            seat.identity_confirmed = True
            room.confirmed_participant_qqs.add(visitor.qq)
            room.participant_names[visitor.qq] = visitor.display_name
            self._sync_primary_player(room)

    @staticmethod
    def _difficulty_label(difficulty: Difficulty) -> str:
        return {"easy": "简单", "normal": "普通", "hard": "困难"}[difficulty]

    def _require_xiangqi_engine(self) -> PikafishService:
        if self.xiangqi_engine is None:
            raise RuntimeError("象棋引擎服务尚未配置")
        return self.xiangqi_engine

    def _is_bot_turn(self, room: GameRoom) -> bool:
        return bool(
            room.game
            and not isinstance(
                room.game, (TurtleSoupGame, DrawGuessGame, BlackjackGame)
            )
            and self._game_is_bot_turn(room.game)
        )

    @staticmethod
    def _game_is_bot_turn(
        game: GomokuGame
        | XiangqiGame
        | TicTacToeGame
        | PigDiceGame
        | BlackjackGame,
    ) -> bool:
        if isinstance(game, BlackjackGame):
            return False
        if isinstance(game, PigDiceGame):
            return game.turn == "bot"
        if isinstance(game, XiangqiGame):
            return game.turn == game.bot_side
        if isinstance(game, GomokuGame):
            return game.turn == game.bot_color
        return game.turn == game.bot_mark

    @staticmethod
    def _game_human_won(
        game: (
            GomokuGame
            | XiangqiGame
            | TicTacToeGame
            | TurtleSoupGame
            | PigDiceGame
            | DrawGuessGame
        ),
    ) -> bool:
        if isinstance(game, PigDiceGame):
            return game.winner == "human"
        if isinstance(game, TurtleSoupGame):
            return game.winner == "human"
        if isinstance(game, DrawGuessGame):
            return game.solved
        if isinstance(game, XiangqiGame):
            return game.winner == game.human_side
        if isinstance(game, GomokuGame):
            return game.winner == game.human_color
        return game.winner == game.human_mark

    @staticmethod
    def _game_label(game_type: GameType) -> str:
        return {
            "gomoku": "五子棋",
            "xiangqi": "中国象棋",
            "tictactoe": "井字棋",
            "turtle_soup": "海龟汤",
            "pig_dice": "贪心骰子",
            "draw_guess": "你画我猜",
            "blackjack": "二十一点",
        }[game_type]

    @staticmethod
    def _turtle_soup_game(room: GameRoom) -> TurtleSoupGame:
        if room.game_type != "turtle_soup" or not isinstance(room.game, TurtleSoupGame):
            raise ValueError("当前房间不是海龟汤")
        return room.game

    def _require_turtle_soup_player(
        self,
        room: GameRoom,
        *,
        source: str,
        visitor_token: str,
        actor_qq: str,
    ) -> int:
        expected_token = (
            room.multiplayer.current_token
            if room.multiplayer.enabled
            else room.player_token
        )
        if source == "web":
            visitor = self._visitor(room, visitor_token)
            if visitor.token != expected_token:
                raise PermissionError("还没有轮到你这个当前玩家推进海龟汤")
            return visitor.number
        if room.multiplayer.enabled:
            seat = room.multiplayer.seat_for_qq(actor_qq)
            if seat is None:
                raise PermissionError("你的 QQ 尚未绑定到玩家席，请在 WebUI 操作")
            if seat.visitor_token != expected_token:
                raise PermissionError("还没有轮到你这个当前玩家推进海龟汤")
            visitor = room.visitors.get(seat.visitor_token)
            if visitor is None:
                raise PermissionError("绑定的玩家席已经失效")
            return visitor.number
        if not room.player_qq or str(actor_qq or "") != room.player_qq:
            raise PermissionError("只有当前玩家可以推进海龟汤")
        player = room.player
        if player is None:
            raise PermissionError("当前玩家席已经失效")
        return player.number
