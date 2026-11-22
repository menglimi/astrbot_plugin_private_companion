"""Durable, scope-bound V2 chat game operations."""

from __future__ import annotations

import asyncio
import copy
from collections import Counter, OrderedDict
import hashlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import random
import secrets
import threading
import time
import uuid
from typing import Any

from ..blackjack import BlackjackCard, BlackjackGame, RANKS, SUITS, fresh_shoe
from ..draw_guess import DrawGuessGame, DrawWord, WORD_BANK, normalize_strokes
from ..gomoku import BLACK, WHITE, GomokuGame
from ..pig_dice import PigDiceGame
from ..tictactoe import NOUGHT, X, TicTacToeGame
from ..turtle_soup import (SoupPuzzle, TurtleSoupGame, clean_player_text, fallback_puzzle,
                           normalize_content_level, puzzle_from_mapping)
from ..xiangqi import BLACK as XIANGQI_BLACK, RED as XIANGQI_RED, XiangqiGame
from ..pikafish import MOVE_PATTERN
from .xiangqi import MAX_MOVES as XIANGQI_MAX_MOVES
from .xiangqi import outcome as xiangqi_outcome, replay as replay_xiangqi, view as xiangqi_view
from .context import SNAPSHOT_ACTIONS, conversation_records, public_game_fact, stamp_game_changes

logger = logging.getLogger(__name__)


SCHEMA = "companion.native-provider.v1"
PLUGIN_ID = "astrbot_plugin_private_companion.game"
PROVIDER_ID = "game.native"
READ = "game.tictactoe.snapshot"
CREATE = "game.tictactoe.create"
MOVE = "game.tictactoe.move"
CLOSE = "game.tictactoe.close"
GOMOKU_READ = "game.gomoku.snapshot"
GOMOKU_CREATE = "game.gomoku.create"
GOMOKU_MOVE = "game.gomoku.move"
GOMOKU_CLOSE = "game.gomoku.close"
PIG_READ = "game.pig_dice.snapshot"
PIG_CREATE = "game.pig_dice.create"
PIG_ROLL = "game.pig_dice.roll"
PIG_HOLD = "game.pig_dice.hold"
PIG_CLOSE = "game.pig_dice.close"
BLACKJACK_READ = "game.blackjack.snapshot"
BLACKJACK_CREATE = "game.blackjack.create"
BLACKJACK_HIT = "game.blackjack.hit"
BLACKJACK_STAND = "game.blackjack.stand"
BLACKJACK_CLOSE = "game.blackjack.close"
XIANGQI_READ = "game.xiangqi.snapshot"
XIANGQI_CREATE = "game.xiangqi.create"
XIANGQI_MOVE = "game.xiangqi.move"
XIANGQI_CLOSE = "game.xiangqi.close"
TURTLE_READ = "game.turtle_soup.snapshot"
TURTLE_PRIVATE_CONTEXT = "game.turtle_soup.private_context"
TURTLE_CREATE = "game.turtle_soup.create"
TURTLE_ASK = "game.turtle_soup.ask"
TURTLE_ANSWER = "game.turtle_soup.answer"
TURTLE_HINT = "game.turtle_soup.hint"
TURTLE_GIVE_UP = "game.turtle_soup.give_up"
TURTLE_CLOSE = "game.turtle_soup.close"
DRAW_READ = "game.draw_guess.snapshot"
DRAW_CREATE = "game.draw_guess.create"
DRAW_GUESS = "game.draw_guess.guess"
DRAW_GIVE_UP = "game.draw_guess.give_up"
DRAW_CLOSE = "game.draw_guess.close"
TICTACTOE_ACTIONS = {READ, CREATE, MOVE, CLOSE}
GOMOKU_ACTIONS = {GOMOKU_READ, GOMOKU_CREATE, GOMOKU_MOVE, GOMOKU_CLOSE}
XIANGQI_ACTIONS = {XIANGQI_READ, XIANGQI_CREATE, XIANGQI_MOVE, XIANGQI_CLOSE}
TURTLE_ACTIONS = {TURTLE_READ, TURTLE_PRIVATE_CONTEXT, TURTLE_CREATE, TURTLE_ASK,
                  TURTLE_ANSWER, TURTLE_HINT, TURTLE_GIVE_UP, TURTLE_CLOSE}
DRAW_ACTIONS = {DRAW_READ, DRAW_CREATE, DRAW_GUESS, DRAW_GIVE_UP, DRAW_CLOSE}
BOARD_GAME_KINDS = {"tictactoe", "gomoku"}
WEB_INTERACT = "web_interact"
WEB_REPLY = "web_reply"
WEB_DRAW_STROKES = "web_draw_strokes"
WEB_DRAW_UNDO = "web_draw_undo"
WEB_DRAW_CLEAR = "web_draw_clear"
WEB_DRAW_BOT_GUESS = "web_draw_bot_guess"
WEB_DRAW_ACTIONS = {WEB_DRAW_STROKES, WEB_DRAW_UNDO, WEB_DRAW_CLEAR, WEB_DRAW_BOT_GUESS}
ACTIONS = (TICTACTOE_ACTIONS | GOMOKU_ACTIONS | {PIG_READ, PIG_CREATE, PIG_ROLL, PIG_HOLD, PIG_CLOSE}
           | {BLACKJACK_READ, BLACKJACK_CREATE, BLACKJACK_HIT, BLACKJACK_STAND, BLACKJACK_CLOSE}
           | XIANGQI_ACTIONS | TURTLE_ACTIONS | DRAW_ACTIONS)
READ_ACTIONS = {READ, GOMOKU_READ, PIG_READ, BLACKJACK_READ, XIANGQI_READ,
                TURTLE_READ, TURTLE_PRIVATE_CONTEXT, DRAW_READ}
CREATE_ACTIONS = {CREATE, GOMOKU_CREATE, PIG_CREATE, BLACKJACK_CREATE, XIANGQI_CREATE,
                  TURTLE_CREATE, DRAW_CREATE}
MOVE_ACTIONS = {MOVE, GOMOKU_MOVE, XIANGQI_MOVE}
CLOSE_ACTIONS = {CLOSE, GOMOKU_CLOSE, PIG_CLOSE, BLACKJACK_CLOSE, XIANGQI_CLOSE,
                 TURTLE_CLOSE, DRAW_CLOSE}
TURTLE_MAX_EVENTS = 160
XIANGQI_CACHE_SIZE = 64
WEB_TICKET_TTL_SECONDS = 12 * 60 * 60
WEB_TICKET_LIMIT = 256


def _kind(action: str) -> str:
    if action in XIANGQI_ACTIONS:
        return "xiangqi"
    if action in TURTLE_ACTIONS:
        return "turtle_soup"
    if action in DRAW_ACTIONS:
        return "draw_guess"
    if action in GOMOKU_ACTIONS:
        return "gomoku"
    if action in {PIG_READ, PIG_CREATE, PIG_ROLL, PIG_HOLD, PIG_CLOSE}:
        return "pig_dice"
    if action in {BLACKJACK_READ, BLACKJACK_CREATE, BLACKJACK_HIT, BLACKJACK_STAND, BLACKJACK_CLOSE}:
        return "blackjack"
    return "tictactoe"


def _record_kind(record: dict[str, Any]) -> str:
    return record.get("game_type", "tictactoe")


class NativeGameError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _context(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)

    def normalize(item: Any) -> None:
        if isinstance(item, dict):
            item.pop("policy_revision", None)
            for child in item.values():
                normalize(child)
        elif isinstance(item, list):
            for child in item:
                normalize(child)

    normalize(result)
    return result


def _identity(request: dict[str, Any]) -> str:
    keys = ("schema", "invocation_id", "parent_task_id", "tool_call_id", "action",
            "origin_key", "binding", "arguments", "arguments_hash")
    return _hash({key: request[key] for key in keys} | {"context": _context(request["context"])})


def _finish_blackjack(game: BlackjackGame) -> None:
    if game.finished or not game.all_players_done():
        return
    game.phase = "dealer_turn"
    game.reveal_dealer()
    if game.has_pending_hands():
        while game.dealer_must_hit:
            game.draw_dealer()
    game.settle()


def _blackjack_outcome(game: BlackjackGame) -> str:
    result = game.results[1]
    return "player" if result in {"win", "blackjack_win"} else "draw" if result == "push" else "bot"


def _puzzle_document(puzzle) -> dict[str, Any]:
    return {"title": puzzle.title, "surface": puzzle.surface, "solution": puzzle.solution,
            "key_facts": list(puzzle.key_facts), "acceptable_variants": list(puzzle.acceptable_variants),
            "hints": list(puzzle.hints), "theme": puzzle.theme, "trick": puzzle.trick}


def _matched_facts(value: Any, *, count: int) -> set[int]:
    if (type(value) is not list or len(value) > count
            or any(type(item) is not int or not 0 <= item < count for item in value)):
        raise ValueError("invalid matched facts")
    return set(value)


def _turtle_game(record: dict[str, Any]) -> TurtleSoupGame:
    if (record.get("difficulty") not in {"easy", "normal", "hard"}
            or record.get("content_level") not in {"all_ages", "normal", "unrestricted"}
            or type(record.get("max_hints")) is not int or not 0 <= record["max_hints"] <= 8
            or type(record.get("events")) is not list or len(record["events"]) > TURTLE_MAX_EVENTS):
        raise ValueError("invalid turtle soup record")
    puzzle = puzzle_from_mapping(record.get("puzzle", {}), content_level=record["content_level"])
    game = TurtleSoupGame(record["difficulty"], record["max_hints"], record["content_level"])
    game.set_puzzle(puzzle)
    for event in record["events"]:
        if type(event) is not dict or type(event.get("action")) is not str:
            raise ValueError("invalid turtle soup event")
        action = event["action"]
        if action == "ask" and set(event) == {"action", "question", "verdict", "matched_facts"}:
            question = clean_player_text(event["question"], limit=200)
            if event["verdict"] not in {"yes", "no", "irrelevant", "partial", "compound"}:
                raise ValueError("invalid turtle soup verdict")
            facts = _matched_facts(event["matched_facts"], count=len(puzzle.key_facts))
            game.record_question(question, event["verdict"], source="qq", matched_facts=facts)
        elif action == "answer" and set(event) == {"action", "answer", "solved", "matched_facts"}:
            answer = clean_player_text(event["answer"], limit=800)
            if type(event["solved"]) is not bool:
                raise ValueError("invalid turtle soup judgment")
            facts = _matched_facts(event["matched_facts"], count=len(puzzle.key_facts))
            game.record_answer(answer, solved=event["solved"], source="qq", matched_facts=facts)
        elif action == "hint" and set(event) == {"action"}:
            game.reveal_hint(source="qq")
        elif action == "give_up" and set(event) == {"action"}:
            game.give_up(source="qq")
        else:
            raise ValueError("invalid turtle soup event")
    return game


def _draw_game(record: dict[str, Any]) -> DrawGuessGame:
    target = record.get("target")
    started_at = record.get("started_at")
    raw_strokes = record.get("strokes", [])
    revision = record.get("revision", 0)
    if (record.get("difficulty") not in {"easy", "normal", "hard"}
            or type(record.get("max_guesses")) is not int or not 1 <= record["max_guesses"] <= 10
            or type(record.get("duration_seconds")) is not int or not 10 <= record["duration_seconds"] <= 600
            or type(started_at) not in (int, float) or not math.isfinite(started_at)
            or type(target) is not dict or set(target) != {"answer", "aliases"}
            or type(target["answer"]) is not str or not target["answer"] or len(target["answer"]) > 80
            or type(target["aliases"]) is not list or len(target["aliases"]) > 8
            or any(type(alias) is not str or not alias or len(alias) > 80 for alias in target["aliases"])
            or type(record.get("guesses")) is not list or len(record["guesses"]) > record["max_guesses"]
            or type(record.get("gave_up")) is not bool or type(record.get("timed_out")) is not bool
            or type(revision) is not int or revision < 0):
        raise ValueError("invalid draw guess record")
    strokes = normalize_strokes(raw_strokes)
    game = DrawGuessGame(record["difficulty"], record["max_guesses"], record["duration_seconds"],
                         DrawWord(target["answer"], tuple(target["aliases"])), started_at=float(started_at),
                         strokes=strokes, revision=revision)
    for event in record["guesses"]:
        if (type(event) is not dict or set(event) != {"guess", "correct"}
                or type(event["guess"]) is not str or type(event["correct"]) is not bool
                or game.finished or game.matches(event["guess"]) is not event["correct"]):
            raise ValueError("invalid draw guess event")
        game.record_guess(event["guess"], correct=event["correct"])
    if record["timed_out"]:
        if game.finished or record["gave_up"]:
            raise ValueError("invalid draw guess timeout")
        game.timeout()
    elif record["gave_up"]:
        if game.finished:
            raise ValueError("invalid draw guess resignation")
        game.finished = True
    return game


def _game(record: dict[str, Any]) -> (TicTacToeGame | GomokuGame | PigDiceGame | BlackjackGame
                                    | TurtleSoupGame | DrawGuessGame):
    kind = _record_kind(record)
    if kind == "turtle_soup":
        return _turtle_game(record)
    if kind == "draw_guess":
        return _draw_game(record)
    if kind not in {"tictactoe", "gomoku", "pig_dice", "blackjack"}:
        raise ValueError("invalid game type")
    if kind == "blackjack":
        cards = record["shoe"]
        events = record["events"]
        if (not isinstance(cards, list) or len(cards) != 312 or not isinstance(events, list)
                or len(events) > 308):
            raise ValueError("invalid blackjack ledger")
        shoe = [BlackjackCard(card["rank"], card["suit"]) for card in cards]
        if Counter((card.rank, card.suit) for card in shoe) != Counter(
                {(rank, suit): 6 for rank in RANKS for suit in SUITS}):
            raise ValueError("invalid blackjack shoe")
        game = BlackjackGame.deal(difficulty=record["difficulty"], player_numbers=[1], shoe=shoe)
        _finish_blackjack(game)
        for action in events:
            if game.finished:
                raise ValueError("blackjack action after settlement")
            if action == "hit":
                game.hit(1)
            elif action == "stand":
                game.stand(1)
            else:
                raise ValueError("invalid blackjack action")
            _finish_blackjack(game)
        return game
    if kind == "pig_dice":
        return _pig_dice_state(record)[0]
    human_side = record.get("human_side", "black")
    if kind == "gomoku" and human_side not in {"black", "white"}:
        raise ValueError("invalid gomoku side")
    game = (GomokuGame(human_color=BLACK if human_side == "black" else WHITE, difficulty=record["difficulty"])
            if kind == "gomoku" else TicTacToeGame(difficulty=record["difficulty"]))
    moves = record["moves"]
    for index, (row, column, mark) in enumerate(moves):
        next_turn = moves[index + 1][2] if index + 1 < len(moves) else None
        game.place_for_fun(row, column, mark, next_turn=next_turn)
    return game


def _pig_dice_state(record: dict[str, Any]) -> tuple[PigDiceGame, dict[str, int]]:
    target_score = record.get("target_score", 50)
    if type(target_score) is not int or not 20 <= target_score <= 200:
        raise ValueError("invalid pig dice target score")
    game = PigDiceGame(difficulty=record["difficulty"], target_score=target_score, turn="human")
    events = record["events"]
    if not isinstance(events, list) or len(events) > 1024:
        raise ValueError("invalid dice events")
    pending = {"human": 0, "bot": 0}
    for event in events:
        if type(event) is not list or len(event) != 3:
            raise ValueError("invalid dice event")
        actor, action, value = event
        if actor not in {"human", "bot"} or type(action) is not str:
            raise ValueError("invalid dice event")
        if (action == "cheat_next" and actor == "human" and value == "bot"
                and game.turn == "human"):
            pending["bot"] += 1
            continue
        if action == "cup_roll":
            if (type(value) is not list or len(value) != 2
                    or any(type(point) is not int or not 1 <= point <= 6 for point in value)
                    or (value[0] != 1 and value[1] != value[0]) or pending[actor] != 0):
                raise ValueError("invalid dice event")
            game.roll(actor, value=value[1])
            continue
        if type(value) is not int:
            raise ValueError("invalid dice event")
        if action == "roll" and 1 <= value <= 6 and pending[actor] == 0:
            game.roll(actor, value=value)
        elif action == "cheat_roll" and value == 6:
            if actor == "bot":
                if pending[actor] == 0:
                    raise ValueError("invalid dice event")
                pending[actor] -= 1
            game.roll(actor, value=value)
        elif action == "hold" and value == 0 and pending[actor] == 0:
            game.hold(actor)
        else:
            raise ValueError("invalid dice event")
    return game, pending


def _piece_id(index: int) -> str:
    return f"piece-{index + 1}"


def _board_pieces(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose stable board-piece identities and persisted free placement."""
    positions = record.get("piece_positions")
    if not isinstance(positions, dict):
        positions = {}
    pieces: list[dict[str, Any]] = []
    for index, move in enumerate(record.get("moves", [])):
        row, column, color = move
        piece_id = _piece_id(index)
        position = positions.get(piece_id)
        if not isinstance(position, dict):
            position = {}
        visual_row = position.get("row", row)
        visual_column = position.get("column", column)
        x = position.get("x", 0.5)
        y = position.get("y", 0.5)
        if (type(visual_row) is not int or type(visual_column) is not int
                or type(x) not in (int, float) or isinstance(x, bool)
                or type(y) not in (int, float) or isinstance(y, bool)
                or not 0 <= x <= 1 or not 0 <= y <= 1):
            visual_row, visual_column, x, y = row, column, 0.5, 0.5
        pieces.append({"piece_id": piece_id, "color": color,
                       "row": visual_row, "column": visual_column,
                       "x": round(float(x), 4), "y": round(float(y), 4)})
    return pieces


def _view(record: dict[str, Any], *, viewer: str = "guesser", include_strokes: bool = False) -> dict[str, Any]:
    game = _game(record)
    kind = _record_kind(record)
    if kind == "turtle_soup":
        state = game.snapshot()
        for entry in state["entries"]:
            entry.pop("at", None)
        result = {"game_id": record["game_id"], "game_type": kind, "status": record["status"],
                  "state": state, "events": copy.deepcopy(record["events"][-24:]),
                  "outcome": record.get("outcome")}
        return result
    if kind == "draw_guess":
        is_drawer = viewer == "drawer"
        reveal_answer = is_drawer or (record["status"] == "finished")
        state = game.snapshot(reveal_answer=reveal_answer)
        for guess in state["guesses"]:
            guess.pop("at", None)
        state.pop("started_at", None)
        state.pop("deadline", None)
        state["viewer_role"] = "drawer" if is_drawer else "guesser"
        state["stroke_count"] = len(game.strokes)
        state["has_drawing"] = bool(game.strokes)
        if not include_strokes:
            state["strokes"] = []
        return {"game_id": record["game_id"], "game_type": kind, "status": record["status"],
                "state": state, "events": copy.deepcopy(record["guesses"][-24:]),
                "outcome": record.get("outcome")}
    state = game.snapshot()
    if kind in BOARD_GAME_KINDS:
        state["pieces"] = _board_pieces(record)
    if kind == "blackjack":
        state.pop("history")
    return {"game_id": record["game_id"], "game_type": kind,
            "status": record["status"], "state": state,
            "events" if kind in {"pig_dice", "blackjack"} else "moves":
                copy.deepcopy(record["events"][-24:] if kind in {"pig_dice", "blackjack"} else record["moves"]),
            "interactions": copy.deepcopy(record.get("interactions", [])[-24:])
            if kind in BOARD_GAME_KINDS else [],
            "outcome": record.get("outcome")}


def _turtle_private_context(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None or record["status"] != "active":
        return {"game_id": None, "judge_context": None}
    game = _turtle_game(record)
    if game.puzzle is None:
        return {"game_id": record["game_id"], "judge_context": None}
    return {"game_id": record["game_id"], "judge_context": {
        "puzzle": _puzzle_document(game.puzzle),
        "recent_questions": [{"question": entry.prompt, "answer": entry.response}
                             for entry in game.entries[-40:] if entry.kind == "question"]}}


class GameStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"version": 1, "scopes": {}, "operations": {}}
        except (OSError, ValueError, UnicodeError) as exc:
            raise NativeGameError("game_store_invalid") from exc
        if (not isinstance(data, dict) or data.get("version") != 1
                or not isinstance(data.get("scopes"), dict)
                or not isinstance(data.get("operations"), dict)):
            raise NativeGameError("game_store_invalid")
        try:
            for scope, rooms in data["scopes"].items():
                if not isinstance(scope, str) or len(scope) != 64:
                    raise ValueError()
                if not isinstance(rooms, dict):
                    raise ValueError()
                active = {kind: 0 for kind in ("tictactoe", "gomoku", "pig_dice", "blackjack",
                                                "xiangqi", "turtle_soup", "draw_guess")}
                for game_id, record in rooms.items():
                    if not isinstance(record, dict) or game_id != record["game_id"]:
                        raise ValueError()
                    if record["difficulty"] not in {"easy", "normal", "hard"}:
                        raise ValueError()
                    kind = _record_kind(record)
                    if kind == "xiangqi":
                        moves = record.get("moves")
                        side = record.get("human_side")
                        if (side not in {XIANGQI_RED, XIANGQI_BLACK} or type(moves) is not list
                                or len(moves) > XIANGQI_MAX_MOVES or any(type(move) is not str or
                                MOVE_PATTERN.fullmatch(move) is None for move in moves)
                                or record.get("status") not in {"active", "finished", "closed"}
                                or record.get("outcome") not in {None, "player", "bot", "draw"}
                                or (record["status"] == "finished") !=
                                (record.get("outcome") in {"player", "bot", "draw"})
                                or (record["status"] in {"active", "closed"} and
                                    len(moves) % 2 != (0 if side == XIANGQI_RED else 1))):
                            raise ValueError()
                        game = None
                    else:
                        game = _game(record)
                    active[kind] += record["status"] == "active"
                    if game is not None and record["status"] == "active" and game.finished:
                        raise ValueError()
                    if game is not None and record["status"] == "finished" and not game.finished:
                        raise ValueError()
                    if kind == "pig_dice" and record["status"] == "finished" and record.get("outcome") != (
                            "player" if game.winner == "human" else "bot"):
                        raise ValueError()
                    if kind == "blackjack" and record["status"] == "finished" and record.get("outcome") != (
                            _blackjack_outcome(game)):
                        raise ValueError()
                    if kind in {"turtle_soup", "draw_guess"}:
                        expected = "player" if game.solved else "bot"
                        if ((record["status"] == "finished" and record.get("outcome") != expected)
                                or (record["status"] != "finished" and record.get("outcome") is not None)
                                or (record["status"] == "closed" and game.finished)):
                            raise ValueError()
                    if record["status"] not in {"active", "finished", "closed"}:
                        raise ValueError()
                if any(count > 1 for count in active.values()):
                    raise ValueError()
            for invocation_id, operation in data["operations"].items():
                if (not isinstance(invocation_id, str) or not isinstance(operation, dict)
                        or operation.get("scope") not in data["scopes"]
                        or not isinstance(operation.get("identity"), str)
                        or len(operation["identity"]) != 64
                        or not isinstance(operation.get("id"), str)
                        or operation.get("state") not in {"succeeded", "failed"}
                        or operation.get("effect") != (
                            "committed" if operation.get("state") == "succeeded" else "not_committed")
                        or (operation.get("state") == "succeeded") != isinstance(operation.get("result"), dict)):
                    raise ValueError()
            _json(data)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise NativeGameError("game_store_invalid") from exc
        self.data = data

    def commit(self, draft: dict[str, Any]) -> None:
        stamp_game_changes(self.data["scopes"], draft["scopes"], time.time())
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_json(draft))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            self.data = draft
        finally:
            temporary.unlink(missing_ok=True)


class NativeGameProvider:
    def __init__(self, plugin: Any, data_dir: Path, *, version: str):
        self.plugin = plugin
        self.version = version
        source = Path(__file__)
        artifact_hash = hashlib.sha256(b"".join(path.read_bytes() for path in
            (source, source.parent.parent / "tictactoe.py", source.parent.parent / "gomoku.py",
             source.parent.parent / "pig_dice.py", source.parent.parent / "blackjack.py",
             source.parent.parent / "xiangqi.py", source.parent.parent / "pikafish.py",
             source.parent / "xiangqi.py", source.parent.parent / "turtle_soup.py",
             source.parent.parent / "turtle_soup_ai.py",
             source.parent.parent / "draw_guess.py", source.parent / "context.py"))).hexdigest()
        self.artifact_hash = artifact_hash
        self.generation = "game-" + _hash({"version": version, "artifact": artifact_hash})
        self.store = GameStore(Path(data_dir) / "native_v2" / "games.json")
        self._async_lock = asyncio.Lock()
        self._xiangqi_cache: OrderedDict[str, tuple[str, XiangqiGame]] = OrderedDict()
        self._web_sessions: dict[str, dict[str, Any]] = {}
        self._game_contexts: dict[str, Any] = {}
        self._scope_contexts: dict[str, Any] = {}
        self._web_terminal_callback: Any = None
        self.closed = False

    def bind_game_context(self, game_id: str, context: Any, *, scope: str | None = None) -> None:
        """Bind a live V2 TaskContext in memory for web-terminal memory writeback."""
        if self.closed or not game_id or context is None:
            return
        self._game_contexts[str(game_id)] = context
        if len(self._game_contexts) > WEB_TICKET_LIMIT * 2:
            oldest = next(iter(self._game_contexts))
            self._game_contexts.pop(oldest, None)
        if scope:
            self._scope_contexts[str(scope)] = context

    def attach_web_terminal_callback(self, callback: Any) -> None:
        self._web_terminal_callback = callback if callable(callback) else None

    async def _notify_web_terminal(self, scope: str, game_id: str, game_view: dict[str, Any]) -> None:
        callback = self._web_terminal_callback
        if callback is None:
            return
        context = self._game_contexts.get(game_id) or self._scope_contexts.get(scope)
        if context is None:
            return
        try:
            if inspect.iscoroutinefunction(callback):
                await callback(context, copy.deepcopy(game_view))
            else:
                result = await asyncio.to_thread(callback, context, copy.deepcopy(game_view))
                if inspect.isawaitable(result):
                    await result
        except Exception as exc:
            logger.warning("[CompanionV2][Game] 网页终局记忆回写失败 game_id=%s error=%s", game_id, exc)

    def manifest(self) -> dict[str, Any]:
        if self.closed:
            raise NativeGameError("game_provider_stopped")
        empty = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        game_id = {"type": "object", "properties": {"game_id": {"type": "string"}},
                   "required": ["game_id"], "additionalProperties": False}
        tools = []
        for kind, enabled, read, create, move, close, last in (
            ("井字棋", True,
             READ, CREATE, MOVE, CLOSE, 2),
            ("五子棋", True,
             GOMOKU_READ, GOMOKU_CREATE, GOMOKU_MOVE, GOMOKU_CLOSE, 14),
        ):
            if not enabled:
                continue
            tools.extend([
                {"action": read, "capability": read, "source_owner": PROVIDER_ID,
                 "description": f"读取当前身份的{kind}对局及棋盘。", "parameters": empty,
                 "side_effect": "none", "timeout_seconds": 10},
                {"action": create, "capability": create, "source_owner": PROVIDER_ID,
                 "description": f"为当前身份开始{kind}并返回可用房间链接；已有未结束对局时接续原局。"
                     + ("human_side 选择当前发言用户的颜色，不是你的颜色；用户让你执黑或先下时传 white。" if create == GOMOKU_CREATE else ""),
                 "parameters": {"type": "object", "properties": {
                     "difficulty": {"type": "string", "enum": ["easy", "normal", "hard"]},
                     **({"human_side": {"type": "string", "enum": ["black", "white"],
                         "description": "用户（当前发言者）的执棋颜色。用户让你或当前角色执黑先下时填 white；用户说我执黑或我先下时填 black。未安排颜色时默认用户执黑。"}}
                        if create == GOMOKU_CREATE else {})},
                     "required": ["difficulty"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 60},
                {"action": move, "capability": move, "source_owner": PROVIDER_ID,
                 "description": f"在当前身份的{kind}对局落子；行列从 0 到 {last}，Bot 随后应手。",
                 "parameters": {"type": "object", "properties": {
                     "game_id": {"type": "string"},
                     "row": {"type": "integer", "enum": list(range(last + 1))},
                     "column": {"type": "integer", "enum": list(range(last + 1))}},
                     "required": ["game_id", "row", "column"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": close, "capability": close, "source_owner": PROVIDER_ID,
                 "description": f"结束当前身份的指定{kind}对局。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
            ])
        if True:
            tools.extend([
                {"action": PIG_READ, "capability": PIG_READ, "source_owner": PROVIDER_ID,
                 "description": "读取当前身份的掷骰竞分对局、分数和回合。", "parameters": empty,
                 "side_effect": "none", "timeout_seconds": 10},
                {"action": PIG_CREATE, "capability": PIG_CREATE, "source_owner": PROVIDER_ID,
                 "description": "开始或接续当前身份的掷骰竞分对局，先达到 50 分获胜。",
                 "parameters": {"type": "object", "properties": {
                     "difficulty": {"type": "string", "enum": ["easy", "normal", "hard"]}},
                     "required": ["difficulty"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": PIG_ROLL, "capability": PIG_ROLL, "source_owner": PROVIDER_ID,
                 "description": "当前玩家回合掷骰一次；可直接掷，或沿骰盅边缘送出。骰子最初停在 1 时，碰杯沿可让它再滚一次，按最后点数结算。",
                 "parameters": {"type": "object", "properties": {
                     "game_id": {"type": "string"},
                     "method": {"type": "string", "enum": ["table", "cup"]}},
                     "required": ["game_id"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": PIG_HOLD, "capability": PIG_HOLD, "source_owner": PROVIDER_ID,
                 "description": "存下本回合分数并轮到 Bot；没有未存分数时不能存分。",
                 "parameters": game_id, "side_effect": "local", "timeout_seconds": 10},
                {"action": PIG_CLOSE, "capability": PIG_CLOSE, "source_owner": PROVIDER_ID,
                 "description": "关闭当前身份的指定掷骰竞分对局。", "parameters": game_id,
                "side_effect": "local", "timeout_seconds": 10},
            ])
        if True:
            tools.extend([
                {"action": BLACKJACK_READ, "capability": BLACKJACK_READ, "source_owner": PROVIDER_ID,
                 "description": "读取当前身份的二十一点单人对局；结算前不显示庄家底牌。",
                 "parameters": empty, "side_effect": "none", "timeout_seconds": 10},
                {"action": BLACKJACK_CREATE, "capability": BLACKJACK_CREATE, "source_owner": PROVIDER_ID,
                 "description": "开始或接续当前身份的单人二十一点；角色做庄家，无下注。",
                 "parameters": {"type": "object", "properties": {
                     "difficulty": {"type": "string", "enum": ["easy", "normal", "hard"]}},
                     "required": ["difficulty"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": BLACKJACK_HIT, "capability": BLACKJACK_HIT, "source_owner": PROVIDER_ID,
                 "description": "当前玩家在二十一点对局中要一张牌；爆牌或达到 21 点会自动结算。",
                 "parameters": game_id, "side_effect": "local", "timeout_seconds": 10},
                {"action": BLACKJACK_STAND, "capability": BLACKJACK_STAND, "source_owner": PROVIDER_ID,
                 "description": "当前玩家在二十一点对局中停牌，庄家随后补牌并结算。",
                 "parameters": game_id, "side_effect": "local", "timeout_seconds": 10},
                {"action": BLACKJACK_CLOSE, "capability": BLACKJACK_CLOSE, "source_owner": PROVIDER_ID,
                 "description": "关闭当前身份的指定二十一点对局。",
                "parameters": game_id, "side_effect": "local", "timeout_seconds": 10},
            ])
        if True:
            coordinates = {name: {"type": "integer", "enum": list(range(limit))} for name, limit in
                           (("from_row", 10), ("from_column", 9),
                            ("to_row", 10), ("to_column", 9))}
            tools.extend([
                {"action": XIANGQI_READ, "capability": XIANGQI_READ, "source_owner": PROVIDER_ID,
                 "description": "读取当前身份的中国象棋对局、棋盘及玩家合法着法；上方黑棋为第 0 行。",
                 "parameters": empty, "side_effect": "none", "timeout_seconds": 120},
                {"action": XIANGQI_CREATE, "capability": XIANGQI_CREATE, "source_owner": PROVIDER_ID,
                 "description": "开始或接续当前身份的中国象棋；选择玩家执红或执黑，执黑时角色先走。需要已安装 Pikafish。",
                 "parameters": {"type": "object", "properties": {
                     "difficulty": {"type": "string", "enum": ["easy", "normal", "hard"]},
                     "human_side": {"type": "string", "enum": ["red", "black"]}},
                     "required": ["difficulty", "human_side"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 120},
                {"action": XIANGQI_MOVE, "capability": XIANGQI_MOVE, "source_owner": PROVIDER_ID,
                 "description": "按 0 起始行列提交当前身份的中国象棋玩家着法，角色随后应手。",
                 "parameters": {"type": "object", "properties": {
                     "game_id": {"type": "string"}, **coordinates},
                     "required": ["game_id", *coordinates], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 120},
                {"action": XIANGQI_CLOSE, "capability": XIANGQI_CLOSE, "source_owner": PROVIDER_ID,
                 "description": "结束当前身份的指定中国象棋对局。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 120},
            ])
        if True:
            matched_facts = {"type": "array", "items": {"type": "integer"}}
            tools.extend([
                {"action": TURTLE_READ, "capability": TURTLE_READ, "source_owner": PROVIDER_ID,
                 "description": "读取当前身份的海龟汤公开题面与进度；不会返回汤底。",
                 "parameters": empty, "side_effect": "none", "timeout_seconds": 10},
                {"action": TURTLE_PRIVATE_CONTEXT, "capability": TURTLE_PRIVATE_CONTEXT,
                 "source_owner": PROVIDER_ID,
                 "description": "仅供裁判判断使用：读取当前题目的汤底和关键事实，随后只给出裁决，不要复述或暗示隐藏内容。",
                 "parameters": empty, "side_effect": "none", "timeout_seconds": 10},
                {"action": TURTLE_CREATE, "capability": TURTLE_CREATE, "source_owner": PROVIDER_ID,
                 "description": "开始或接续当前身份的海龟汤；开始新局后只公开汤面。",
                 "parameters": empty,
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": TURTLE_ASK, "capability": TURTLE_ASK, "source_owner": PROVIDER_ID,
                 "description": "记录一个海龟汤问题及裁决；先读取裁判上下文，按汤底回答 yes/no/irrelevant/partial/compound。",
                 "parameters": {"type": "object", "properties": {"game_id": {"type": "string"},
                     "question": {"type": "string"},
                     "verdict": {"type": "string", "enum": ["yes", "no", "irrelevant", "partial", "compound"]},
                     "matched_facts": matched_facts},
                     "required": ["game_id", "question", "verdict", "matched_facts"],
                     "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": TURTLE_ANSWER, "capability": TURTLE_ANSWER, "source_owner": PROVIDER_ID,
                 "description": "记录玩家的完整推理及裁决；先读取裁判上下文，只有因果链成立时才判 solved。",
                 "parameters": {"type": "object", "properties": {"game_id": {"type": "string"},
                     "answer": {"type": "string"},
                     "solved": {"type": "boolean"}, "matched_facts": matched_facts},
                     "required": ["game_id", "answer", "solved", "matched_facts"],
                     "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": TURTLE_HINT, "capability": TURTLE_HINT, "source_owner": PROVIDER_ID,
                 "description": "揭示海龟汤的下一条渐进提示。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": TURTLE_GIVE_UP, "capability": TURTLE_GIVE_UP, "source_owner": PROVIDER_ID,
                 "description": "结束并揭晓当前海龟汤汤底。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": TURTLE_CLOSE, "capability": TURTLE_CLOSE, "source_owner": PROVIDER_ID,
                 "description": "关闭当前身份的指定海龟汤对局。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
            ])
        if True:
            tools.extend([
                {"action": DRAW_READ, "capability": DRAW_READ, "source_owner": PROVIDER_ID,
                 "description": "读取当前身份的你画我猜进度；进行中不公开答案。",
                 "parameters": empty, "side_effect": "none", "timeout_seconds": 10},
                {"action": DRAW_CREATE, "capability": DRAW_CREATE, "source_owner": PROVIDER_ID,
                 "description": "开始或接续当前身份的你画我猜；用户发送画作图片后，根据图片提交猜测。",
                 "parameters": {"type": "object", "properties": {"difficulty": {
                     "type": "string", "enum": ["easy", "normal", "hard"]}},
                     "required": ["difficulty"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": DRAW_GUESS, "capability": DRAW_GUESS, "source_owner": PROVIDER_ID,
                 "description": "根据当前消息中的画作提交一次猜测；由执行端校验答案并推进回合。",
                 "parameters": {"type": "object", "properties": {"game_id": {"type": "string"},
                     "guess": {"type": "string"}},
                     "required": ["game_id", "guess"], "additionalProperties": False},
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": DRAW_GIVE_UP, "capability": DRAW_GIVE_UP, "source_owner": PROVIDER_ID,
                 "description": "结束当前你画我猜并揭晓答案。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
                {"action": DRAW_CLOSE, "capability": DRAW_CLOSE, "source_owner": PROVIDER_ID,
                 "description": "关闭当前身份的指定你画我猜对局。", "parameters": game_id,
                 "side_effect": "local", "timeout_seconds": 10},
            ])
        for tool in tools:
            if tool["action"] in CREATE_ACTIONS:
                # A first browser entry may need the existing access tunnel.
                tool["timeout_seconds"] = max(60, tool["timeout_seconds"])
        return {"schema": SCHEMA, "plugin_id": PLUGIN_ID, "provider_id": PROVIDER_ID,
                "generation": self.generation,
                "package": {"extension_id": "game", "package_id": PLUGIN_ID,
                            "publisher_id": "StarfallMark", "package_version": self.version,
                            "artifact_hash": self.artifact_hash,
                            "capabilities": sorted(tool["action"] for tool in tools),
                            "dataset_owners": [PROVIDER_ID]},
                "tools": tools}

    def _request(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if self.closed or not isinstance(request, dict) or request.get("schema") != SCHEMA:
            raise NativeGameError("invalid_game_request")
        required = {"invocation_id", "parent_task_id", "tool_call_id", "action", "origin_key",
                    "context", "binding", "arguments", "arguments_hash", "deadline_at"}
        if not required.issubset(request) or request["action"] not in ACTIONS:
            raise NativeGameError("invalid_game_request")
        if any(not isinstance(request[key], str) or not request[key] or len(request[key]) > 2048
               for key in ("invocation_id", "parent_task_id", "tool_call_id", "origin_key",
                           "arguments_hash", "deadline_at")):
            raise NativeGameError("invalid_game_request")
        context = request["context"]
        if not isinstance(context, dict) or not isinstance(context.get("actor"), dict):
            raise NativeGameError("invalid_game_context")
        actor = context["actor"]
        if (not isinstance(actor.get("subject"), dict)
                or not actor["subject"].get("subject_id")
                or not isinstance(actor.get("principal"), dict)
                or not actor["principal"].get("native_account_id")):
            raise NativeGameError("invalid_game_context")
        if request["binding"] != {"capability": request["action"], "provider": PROVIDER_ID,
                                  "version": self.version, "generation": self.generation}:
            raise NativeGameError("game_binding_mismatch")
        args = request["arguments"]
        if not isinstance(args, dict):
            raise NativeGameError("invalid_game_arguments")
        action = request["action"]
        if action in READ_ACTIONS and args:
            raise NativeGameError("invalid_game_arguments")
        if action in CREATE_ACTIONS:
            create_keys = ({"difficulty", "human_side"} if action == XIANGQI_CREATE else
                           set() if action == TURTLE_CREATE else {"difficulty"})
            allowed_keys = (create_keys, create_keys | {"human_side"}) if action == GOMOKU_CREATE else (create_keys,)
            if (set(args) not in allowed_keys
                    or (action != TURTLE_CREATE and args["difficulty"] not in {"easy", "normal", "hard"})
                    or (action == GOMOKU_CREATE and args.get("human_side", "black") not in {"black", "white"})
                    or (action == XIANGQI_CREATE and args["human_side"] not in {XIANGQI_RED, XIANGQI_BLACK})):
                raise NativeGameError("invalid_game_arguments")
        if action in CLOSE_ACTIONS | {PIG_HOLD, BLACKJACK_HIT, BLACKJACK_STAND,
                                     TURTLE_HINT, TURTLE_GIVE_UP, DRAW_GIVE_UP} and (
                set(args) != {"game_id"} or not isinstance(args["game_id"], str)):
            raise NativeGameError("invalid_game_arguments")
        if (action == PIG_ROLL and (set(args) not in ({"game_id"}, {"game_id", "method"})
                or not isinstance(args.get("game_id"), str)
                or ("method" in args and args["method"] not in {"table", "cup"}))):
            raise NativeGameError("invalid_game_arguments")
        if action in {MOVE, GOMOKU_MOVE} and (set(args) != {"game_id", "row", "column"}
                or not isinstance(args["game_id"], str)
                or any(type(args[key]) is not int or not 0 <= args[key] <=
                       (14 if action == GOMOKU_MOVE else 2) for key in ("row", "column"))):
            raise NativeGameError("invalid_game_arguments")
        if action == XIANGQI_MOVE:
            coordinates = {"from_row": 10, "from_column": 9, "to_row": 10, "to_column": 9}
            if (set(args) != {"game_id", *coordinates} or not isinstance(args["game_id"], str)
                    or any(type(args[key]) is not int or not 0 <= args[key] < limit
                           for key, limit in coordinates.items())):
                raise NativeGameError("invalid_game_arguments")
        if action in {TURTLE_ASK, TURTLE_ANSWER}:
            text_key = "question" if action == TURTLE_ASK else "answer"
            required_keys = {"game_id", text_key, "matched_facts"} | ({"verdict"} if action == TURTLE_ASK else {"solved"})
            text_limit = 200 if action == TURTLE_ASK else 800
            facts = args.get("matched_facts")
            valid_facts = (type(facts) is list and len(facts) <= 8
                           and all(type(item) is int and 0 <= item <= 7 for item in facts)
                           and len(set(facts)) == len(facts))
            if (set(args) != required_keys or not isinstance(args.get("game_id"), str)
                    or not 1 <= len(args["game_id"]) <= 128
                    or type(args.get(text_key)) is not str or not args[text_key].strip()
                    or len(args[text_key]) > text_limit or not valid_facts
                    or (action == TURTLE_ASK and args.get("verdict") not in
                        {"yes", "no", "irrelevant", "partial", "compound"})
                    or (action == TURTLE_ANSWER and type(args.get("solved")) is not bool)):
                raise NativeGameError("invalid_game_arguments")
        if action == DRAW_GUESS and (set(args) != {"game_id", "guess"}
                or not isinstance(args.get("game_id"), str)
                or not 1 <= len(args["game_id"]) <= 128
                or type(args.get("guess")) is not str or not args["guess"].strip()
                or len(args["guess"]) > 80):
            raise NativeGameError("invalid_game_arguments")
        if action in CLOSE_ACTIONS | {PIG_ROLL, PIG_HOLD, BLACKJACK_HIT, BLACKJACK_STAND,
                                     TURTLE_HINT, TURTLE_GIVE_UP, DRAW_GIVE_UP} and (
                not args["game_id"] or len(args["game_id"]) > 128):
            raise NativeGameError("invalid_game_arguments")
        return _hash(_context(context)), args

    @staticmethod
    def _checked(callback) -> None:
        if not callable(callback):
            raise NativeGameError("host_guard_required")
        result = callback()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise NativeGameError("host_guard_must_be_synchronous")
        if result is False:
            raise NativeGameError("host_authority_revoked")

    def _receipt(self, request: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        read = request["action"] in READ_ACTIONS
        return {"schema": SCHEMA, "invocation_id": request["invocation_id"],
                "origin_key": request["origin_key"], "arguments_hash": request["arguments_hash"],
                "binding": request["binding"], "state": operation["state"],
                "effect": "none" if read else operation["effect"],
                "operation_id": None if read else operation["id"],
                "receipt_ref": f"{PROVIDER_ID}.operation:{operation['id']}",
                "result": operation["result"] if operation["state"] == "succeeded" else None,
                "artifacts": []}

    def _apply_board_interaction(
        self, room: dict[str, Any], game: TicTacToeGame | GomokuGame,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit a free-form board interaction without losing the game ledger.

        A tray interaction can explicitly choose either colour. Moving an
        existing piece changes its logical cell and its free in-cell offset;
        the move remains identifiable by its stable sequence-derived ID.
        """
        moves = room.get("moves")
        if not isinstance(moves, list):
            raise NativeGameError("game_turn_limit")
        room.setdefault("piece_positions", {})
        room.setdefault("interactions", [])
        source_kind = args["source_kind"]
        target = (args["row"], args["column"])
        piece_id: str
        previous: tuple[int, int, int] | None = None
        if source_kind == "tray":
            if len(moves) >= (15 * 15 if _record_kind(room) == "gomoku" else 9):
                raise NativeGameError("game_turn_limit")
            color = args["source_color"]
            if any(move[0] == target[0] and move[1] == target[1] for move in moves):
                raise NativeGameError("game_target_occupied")
            piece_id = _piece_id(len(moves))
            moves.append([target[0], target[1], color])
            # Validate the complete replay before exposing the new ledger.
            try:
                updated_game = _game(room)
            except ValueError as exc:
                moves.pop()
                raise NativeGameError("game_illegal_move") from exc
        else:
            piece_id = args["source_piece_id"]
            try:
                index = int(piece_id.removeprefix("piece-")) - 1
            except (AttributeError, TypeError, ValueError) as exc:
                raise NativeGameError("invalid_game_arguments") from exc
            if not 0 <= index < len(moves) or _piece_id(index) != piece_id:
                raise NativeGameError("game_piece_not_found")
            previous = tuple(moves[index])
            if args.get("source_color") is not None and args["source_color"] != previous[2]:
                raise NativeGameError("game_piece_mismatch")
            if any(i != index and move[0] == target[0] and move[1] == target[1]
                   for i, move in enumerate(moves)):
                raise NativeGameError("game_target_occupied")
            moves[index] = [target[0], target[1], previous[2]]
            try:
                updated_game = _game(room)
            except ValueError as exc:
                moves[index] = list(previous)
                raise NativeGameError("game_illegal_move") from exc

        room["piece_positions"][piece_id] = {
            "row": target[0], "column": target[1], "x": args["x"], "y": args["y"],
        }
        room["interactions"].append({
            "action": "interact", "piece_id": piece_id, "source": source_kind,
            "color": moves[int(piece_id.removeprefix("piece-")) - 1][2],
            "from": ([previous[0], previous[1]] if previous is not None else None),
            "to": [target[0], target[1]], "x": args["x"], "y": args["y"],
            "interaction": args["interaction"],
        })
        del room["interactions"][:-128]
        if updated_game.finished:
            room["status"] = "finished"
            human = getattr(updated_game, "human_mark", getattr(updated_game, "human_color", 1))
            winner = getattr(updated_game, "winner", 0)
            room["outcome"] = (
                "player" if winner == human else "bot" if winner else "draw"
            )
        return _view(room)

    @staticmethod
    def _recent_turtle_signatures(rooms: dict[str, Any]) -> list[str]:
        signatures: list[str] = []
        for room in rooms.values():
            if _record_kind(room) != "turtle_soup":
                continue
            puzzle = room.get("puzzle")
            if not isinstance(puzzle, dict):
                continue
            theme = str(puzzle.get("theme") or "").strip().lower()
            trick = str(puzzle.get("trick") or "").strip().lower()
            if theme and trick:
                signature = f"{theme}|{trick}"[:180]
                if signature not in signatures:
                    signatures.append(signature)
        return signatures[-16:]

    async def _prepare_turtle_puzzle(
        self, scope: str, level: str, *, difficulty: str = "normal", session_id: str = ""
    ) -> SoupPuzzle:
        with self.store.lock:
            rooms = self.store.data["scopes"].get(scope, {})
            recent = self._recent_turtle_signatures(rooms)
        generator = getattr(self.plugin, "generate_native_turtle_soup_puzzle", None)
        if callable(generator):
            try:
                generated = await generator(
                    difficulty=difficulty,
                    content_level=level,
                    recent_signatures=recent,
                    session_id=session_id,
                )
                if isinstance(generated, SoupPuzzle):
                    return generated
            except Exception as exc:
                logger.info("[CompanionV2][Game] 动态海龟汤出题失败，回落内置题库: %s", exc)
        return fallback_puzzle(content_level=level, excluded_signatures=set(recent))

    def _apply(
        self,
        draft: dict[str, Any],
        scope: str,
        request: dict[str, Any],
        *,
        viewer: str = "guesser",
        include_strokes: bool = False,
        prepared_puzzle: SoupPuzzle | None = None,
    ) -> dict[str, Any]:
        rooms = draft["scopes"].setdefault(scope, {})
        args = request["arguments"]
        action = request["action"]
        kind = _kind(action)
        if action in {WEB_INTERACT, WEB_REPLY} or action in WEB_DRAW_ACTIONS:
            kind = _record_kind(rooms.get(args["game_id"], {}))
        if action not in CLOSE_ACTIONS and not getattr(self.plugin, f"native_v2_{kind}_enabled", False):
            raise NativeGameError(f"game_{kind}_disabled")
        if action in CREATE_ACTIONS:
            current = next((room for room in rooms.values()
                            if _record_kind(room) == kind and room["status"] == "active"), None)
            if current is None:
                current = {"game_id": "game-" + uuid.uuid4().hex,
                           "difficulty": args.get("difficulty", "normal"),
                           "game_type": kind, "status": "active", "outcome": None}
                if kind == "blackjack":
                    current["shoe"] = [card.as_dict() for card in fresh_shoe()]
                if kind in {"pig_dice", "blackjack"}:
                    current["events"] = []
                    if kind == "pig_dice":
                        raw_target = getattr(
                            self.plugin,
                            "native_v2_pig_dice_target_score",
                            getattr(self.plugin, "pig_dice_target_score", 50),
                        )
                        try:
                            target_score = max(20, min(200, int(raw_target)))
                        except (TypeError, ValueError):
                            target_score = 50
                        current["target_score"] = target_score
                elif kind == "turtle_soup":
                    level = normalize_content_level(getattr(self.plugin,
                        "native_v2_turtle_soup_content_level", "normal"))
                    recent = self._recent_turtle_signatures(rooms)
                    puzzle = prepared_puzzle if isinstance(prepared_puzzle, SoupPuzzle) else fallback_puzzle(
                        content_level=level, excluded_signatures=set(recent))
                    current.update(content_level=level,
                        max_hints=max(0, min(8, int(getattr(self.plugin,
                            "native_v2_turtle_soup_max_hints", 3)))),
                        puzzle=_puzzle_document(puzzle), events=[])
                elif kind == "draw_guess":
                    words = WORD_BANK[args["difficulty"]]
                    target = random.choice(words)
                    current.update(max_guesses=max(1, min(10, int(getattr(self.plugin,
                        "native_v2_draw_guess_max_guesses", 5)))),
                        duration_seconds=max(10, min(600, int(getattr(self.plugin,
                            "native_v2_draw_guess_duration_seconds", 120)))),
                        started_at=time.time(), target={"answer": target.answer,
                            "aliases": list(target.aliases)},
                        strokes=[], revision=0, guesses=[], gave_up=False, timed_out=False)
                else:
                    current["moves"] = []
                    if kind in BOARD_GAME_KINDS:
                        current["piece_positions"] = {}
                        current["interactions"] = []
                    if kind == "gomoku":
                        current["human_side"] = args.get("human_side", "black")
                        if current["human_side"] == "white":
                            game = _game(current)
                            row, column = game.choose_bot_move()
                            current["moves"].append([row, column, game.bot_color])
                if kind == "blackjack":
                    game = _game(current)
                    if game.finished:
                        current["status"] = "finished"
                        current["outcome"] = _blackjack_outcome(game)
                rooms[current["game_id"]] = current
            return _view(current, viewer=viewer, include_strokes=include_strokes)
        room = rooms.get(args["game_id"])
        if room is None or _record_kind(room) != kind:
            raise NativeGameError("game_not_found_in_scope")
        if action in CLOSE_ACTIONS:
            if room["status"] == "active":
                room["status"] = "closed"
            return _view(room, viewer=viewer, include_strokes=include_strokes)
        if room["status"] != "active":
            raise NativeGameError("game_not_active")
        game = _game(room)
        if action == WEB_REPLY and kind not in BOARD_GAME_KINDS:
            raise NativeGameError("invalid_game_arguments")
        if action == WEB_INTERACT:
            if kind not in BOARD_GAME_KINDS:
                raise NativeGameError("invalid_game_arguments")
            return self._apply_board_interaction(room, game, args)
        if kind == "turtle_soup":
            if len(room["events"]) >= TURTLE_MAX_EVENTS:
                raise NativeGameError("game_turn_limit")
            puzzle = game.puzzle
            if puzzle is None:
                raise NativeGameError("game_store_invalid")
            try:
                if action == TURTLE_ASK:
                    question = clean_player_text(args["question"], limit=200)
                    facts = _matched_facts(args["matched_facts"], count=len(puzzle.key_facts))
                    game.record_question(question, args["verdict"], source="qq", matched_facts=facts)
                    room["events"].append({"action": "ask", "question": question,
                        "verdict": args["verdict"], "matched_facts": sorted(facts)})
                elif action == TURTLE_ANSWER:
                    answer = clean_player_text(args["answer"], limit=800)
                    facts = _matched_facts(args["matched_facts"], count=len(puzzle.key_facts))
                    game.record_answer(answer, solved=args["solved"], source="qq", matched_facts=facts)
                    room["events"].append({"action": "answer", "answer": answer,
                        "solved": args["solved"], "matched_facts": sorted(facts)})
                elif action == TURTLE_HINT:
                    game.reveal_hint(source="qq")
                    room["events"].append({"action": "hint"})
                elif action == TURTLE_GIVE_UP:
                    game.give_up(source="qq")
                    room["events"].append({"action": "give_up"})
                else:
                    raise NativeGameError("invalid_game_request")
            except ValueError as exc:
                raise NativeGameError("game_illegal_move") from exc
            if game.finished:
                room["status"] = "finished"
                room["outcome"] = "player" if game.solved else "bot"
            return _view(room)
        if kind == "draw_guess":
            if time.time() >= game.deadline:
                room["timed_out"] = True
                room["status"] = "finished"
                room["outcome"] = "bot"
                return _view(room, viewer=viewer, include_strokes=include_strokes)
            if action == DRAW_GIVE_UP:
                room["gave_up"] = True
                room["status"] = "finished"
                room["outcome"] = "bot"
                return _view(room, viewer=viewer, include_strokes=include_strokes)
            if action == WEB_DRAW_STROKES:
                try:
                    strokes = normalize_strokes(args["strokes"])
                    game.replace_strokes(strokes)
                except ValueError as exc:
                    raise NativeGameError("invalid_game_arguments") from exc
                room["strokes"] = strokes
                room["revision"] = int(room.get("revision", 0)) + 1
                return _view(room, viewer=viewer, include_strokes=include_strokes)
            if action == WEB_DRAW_UNDO:
                strokes = list(room.get("strokes", []))
                if strokes:
                    strokes.pop()
                    room["strokes"] = strokes
                    room["revision"] = int(room.get("revision", 0)) + 1
                return _view(room, viewer=viewer, include_strokes=include_strokes)
            if action == WEB_DRAW_CLEAR:
                if room.get("strokes"):
                    room["strokes"] = []
                    room["revision"] = int(room.get("revision", 0)) + 1
                return _view(room, viewer=viewer, include_strokes=include_strokes)
            if action != DRAW_GUESS:
                raise NativeGameError("invalid_game_request")
            if len(room["guesses"]) >= room["max_guesses"]:
                raise NativeGameError("game_turn_limit")
            guess = clean_player_text(args["guess"], limit=80)
            correct = game.matches(guess)
            try:
                game.record_guess(guess, correct=correct)
            except ValueError as exc:
                raise NativeGameError("game_illegal_move") from exc
            room["guesses"].append({"guess": guess, "correct": correct})
            if game.finished:
                room["status"] = "finished"
                room["outcome"] = "player" if game.solved else "bot"
            return _view(room, viewer=viewer, include_strokes=include_strokes)
        if kind == "blackjack":
            if len(room["events"]) >= 308:
                raise NativeGameError("game_turn_limit")
            try:
                if action == BLACKJACK_HIT:
                    game.hit(1)
                    room["events"].append("hit")
                elif action == BLACKJACK_STAND:
                    game.stand(1)
                    room["events"].append("stand")
                else:
                    raise NativeGameError("invalid_game_request")
                _finish_blackjack(game)
            except ValueError as exc:
                raise NativeGameError("game_illegal_move") from exc
            if game.finished:
                room["status"] = "finished"
                room["outcome"] = _blackjack_outcome(game)
            return _view(room)
        if kind == "pig_dice":
            if len(room["events"]) >= 992:
                raise NativeGameError("game_turn_limit")
            game, pending = _pig_dice_state(room)
            try:
                if action == PIG_ROLL:
                    if args.get("method", "table") == "cup":
                        first, final = game.roll_with_cup("human")
                        room["events"].append(["human", "cup_roll", [first, final]])
                    else:
                        room["events"].append(["human", "roll", game.roll("human")])
                elif action == PIG_HOLD:
                    game.hold("human")
                    room["events"].append(["human", "hold", 0])
                else:
                    raise NativeGameError("invalid_game_request")
                for _ in range(30):
                    if game.finished or game.turn != "bot":
                        break
                    if pending["bot"]:
                        game.roll("bot", value=6)
                        room["events"].append(["bot", "cheat_roll", 6])
                        pending["bot"] -= 1
                    elif game.bot_should_hold():
                        game.hold("bot")
                        room["events"].append(["bot", "hold", 0])
                    else:
                        room["events"].append(["bot", "roll", game.roll("bot")])
                else:
                    raise NativeGameError("game_turn_limit")
            except ValueError as exc:
                raise NativeGameError("game_illegal_move") from exc
            if game.finished:
                room["status"] = "finished"
                room["outcome"] = "player" if game.winner == "human" else "bot"
            return _view(room)
        human, bot = (game.human_color, game.bot_color) if kind == "gomoku" else (X, NOUGHT)
        room.setdefault("piece_positions", {})
        room.setdefault("interactions", [])
        try:
            if action != WEB_REPLY:
                game.place(args["row"], args["column"], human)
                room["moves"].append([args["row"], args["column"], human])
                room["piece_positions"][_piece_id(len(room["moves"]) - 1)] = {
                    "row": args["row"], "column": args["column"],
                    "x": args.get("x", 0.5), "y": args.get("y", 0.5),
                }
            if not game.finished:
                row, column = game.choose_bot_move(seed=int(_hash(request["invocation_id"]), 16))
                game.place(row, column, bot)
                room["moves"].append([row, column, bot])
                room["piece_positions"][_piece_id(len(room["moves"]) - 1)] = {
                    "row": row, "column": column, "x": 0.5, "y": 0.5,
                }
        except ValueError as exc:
            raise NativeGameError("game_illegal_move") from exc
        if game.finished:
            room["status"] = "finished"
            room["outcome"] = "player" if game.winner == human else "bot" if game.winner == bot else "draw"
        return _view(room)

    def _execute_sync(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        scope, _args = self._request(request)
        self._checked(before_dispatch)
        with self.store.lock:
            if request["action"] in READ_ACTIONS:
                kind = _kind(request["action"])
                if not getattr(self.plugin, f"native_v2_{kind}_enabled", False):
                    raise NativeGameError(f"game_{kind}_disabled")
                rooms = self.store.data["scopes"].get(scope, {})
                matching = [room for room in rooms.values() if _record_kind(room) == kind]
                current = next((room for room in matching if room["status"] == "active"), None)
                if current is None and matching:
                    current = matching[-1]
                self._checked(before_dispatch)
                if request["action"] == TURTLE_PRIVATE_CONTEXT:
                    return self._receipt(request, {"id": "snapshot", "state": "succeeded", "effect": "none",
                        "result": _turtle_private_context(current)})
                return self._receipt(request, {"id": "snapshot", "state": "succeeded", "effect": "none",
                                               "result": {"game": _view(current) if current else None}})
            identity = _identity(request)
            previous = self.store.data["operations"].get(request["invocation_id"])
            if previous is not None:
                if previous["identity"] != identity or previous["scope"] != scope:
                    raise NativeGameError("game_original_invocation_mismatch")
                return self._receipt(request, previous)
            draft = copy.deepcopy(self.store.data)
            operation = {"id": "game-op-" + uuid.uuid4().hex, "scope": scope,
                         "identity": identity, "state": "succeeded", "effect": "committed", "result": None}
            try:
                operation["result"] = {"game": self._apply(draft, scope, request)}
            except NativeGameError as exc:
                draft = copy.deepcopy(self.store.data)
                draft["scopes"].setdefault(scope, {})
                operation.update(state="failed", effect="not_committed", error=exc.code)
            draft["operations"][request["invocation_id"]] = operation
            self._checked(before_dispatch)
            self.store.commit(draft)
            return self._receipt(request, operation)

    def _xiangqi_engine(self):
        engine = getattr(self.plugin, "xiangqi_engine", None)
        if engine is None or (callable(getattr(engine, "binary_path", None)) and engine.binary_path() is None):
            raise NativeGameError("game_xiangqi_engine_unavailable")
        return engine

    def _xiangqi_enabled(self, request: dict[str, Any]) -> None:
        if request["action"] != XIANGQI_CLOSE and not getattr(self.plugin, "native_v2_xiangqi_enabled", False):
            raise NativeGameError("game_xiangqi_disabled")

    def _cache_xiangqi(self, room: dict[str, Any], game: XiangqiGame) -> None:
        self._xiangqi_cache[room["game_id"]] = _hash(room), copy.deepcopy(game)
        self._xiangqi_cache.move_to_end(room["game_id"])
        while len(self._xiangqi_cache) > XIANGQI_CACHE_SIZE:
            self._xiangqi_cache.popitem(last=False)

    async def _xiangqi_game(self, room: dict[str, Any]) -> XiangqiGame:
        fingerprint = _hash(room)
        cached = self._xiangqi_cache.get(room["game_id"])
        if cached is not None and cached[0] == fingerprint:
            self._xiangqi_cache.move_to_end(room["game_id"])
            return copy.deepcopy(cached[1])
        try:
            game = await replay_xiangqi(self._xiangqi_engine(), room)
        except ValueError as exc:
            raise NativeGameError("game_store_invalid") from exc
        self._cache_xiangqi(room, game)
        return game

    async def _apply_xiangqi(self, draft: dict[str, Any], scope: str,
                             request: dict[str, Any]) -> tuple[dict[str, Any], XiangqiGame]:
        rooms = draft["scopes"].setdefault(scope, {})
        action, args = request["action"], request["arguments"]
        self._xiangqi_enabled(request)
        if action == XIANGQI_CREATE:
            room = next((room for room in rooms.values() if _record_kind(room) == "xiangqi"
                         and room["status"] == "active"), None)
            if room is not None:
                return room, await self._xiangqi_game(room)
            game = await XiangqiGame.create(self._xiangqi_engine(), human_side=args["human_side"],
                                            difficulty=args["difficulty"])
            if not game.legal_moves:
                raise NativeGameError("game_xiangqi_engine_unavailable")
            if game.human_side == XIANGQI_BLACK:
                await game.place_bot(self._xiangqi_engine())
            room = {"game_id": "game-" + uuid.uuid4().hex, "game_type": "xiangqi",
                    "difficulty": args["difficulty"], "human_side": args["human_side"],
                    "moves": list(game.moves), "status": "finished" if game.finished else "active",
                    "outcome": xiangqi_outcome(game)}
            rooms[room["game_id"]] = room
            return room, game
        room = rooms.get(args["game_id"])
        if room is None or _record_kind(room) != "xiangqi":
            raise NativeGameError("game_not_found_in_scope")
        game = await self._xiangqi_game(room)
        if action == XIANGQI_CLOSE:
            if room["status"] == "active":
                room["status"] = "closed"
            return room, game
        if room["status"] != "active":
            raise NativeGameError("game_not_active")
        if len(game.moves) > XIANGQI_MAX_MOVES - 2:
            raise NativeGameError("game_turn_limit")
        try:
            await game.place_human(self._xiangqi_engine(), args["from_row"], args["from_column"],
                                    args["to_row"], args["to_column"])
        except ValueError as exc:
            raise NativeGameError("game_illegal_move") from exc
        if not game.finished:
            await game.place_bot(self._xiangqi_engine())
        room.update(moves=list(game.moves), status="finished" if game.finished else "active",
                    outcome=xiangqi_outcome(game))
        return room, game

    async def _execute_xiangqi(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        scope, _args = self._request(request)
        self._checked(before_dispatch)
        if request["action"] == XIANGQI_READ:
            self._xiangqi_enabled(request)
            rooms = self.store.data["scopes"].get(scope, {})
            matching = [room for room in rooms.values() if _record_kind(room) == "xiangqi"]
            room = next((room for room in matching if room["status"] == "active"), None)
            if room is None and matching:
                room = matching[-1]
            try:
                game = await self._xiangqi_game(room) if room else None
            except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
                raise NativeGameError("game_xiangqi_engine_unavailable") from exc
            self._request(request)
            self._xiangqi_enabled(request)
            self._checked(before_dispatch)
            return self._receipt(request, {"id": "snapshot", "state": "succeeded", "effect": "none",
                "result": {"game": xiangqi_view(room, game) if room else None}})
        identity = _identity(request)
        previous = self.store.data["operations"].get(request["invocation_id"])
        if previous is not None:
            if previous["identity"] != identity or previous["scope"] != scope:
                raise NativeGameError("game_original_invocation_mismatch")
            return self._receipt(request, previous)
        draft = copy.deepcopy(self.store.data)
        operation = {"id": "game-op-" + uuid.uuid4().hex, "scope": scope, "identity": identity,
                     "state": "succeeded", "effect": "committed", "result": None}
        try:
            room, game = await self._apply_xiangqi(draft, scope, request)
            operation["result"] = {"game": xiangqi_view(room, game)}
        except (NativeGameError, RuntimeError, OSError, asyncio.TimeoutError) as exc:
            # Engine calls only calculate a draft; no half-round is committed on failure.
            draft = copy.deepcopy(self.store.data)
            draft["scopes"].setdefault(scope, {})
            operation.update(state="failed", effect="not_committed",
                             error=exc.code if isinstance(exc, NativeGameError)
                             else "game_xiangqi_engine_unavailable")
        self._request(request)
        self._xiangqi_enabled(request)
        self._checked(before_dispatch)
        with self.store.lock:
            self.store.commit(draft | {"operations": draft["operations"] | {request["invocation_id"]: operation}})
        if operation["state"] == "succeeded":
            self._cache_xiangqi(room, game)
        return self._receipt(request, operation)

    async def _execute_turtle_create(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        async with self._async_lock:
            scope, _args = self._request(request)
            if not getattr(self.plugin, "native_v2_turtle_soup_enabled", False):
                raise NativeGameError("game_turtle_soup_disabled")
            self._checked(before_dispatch)
            identity = _identity(request)
            with self.store.lock:
                previous = self.store.data["operations"].get(request["invocation_id"])
                if previous is not None:
                    if previous["identity"] != identity or previous["scope"] != scope:
                        raise NativeGameError("game_original_invocation_mismatch")
                    return self._receipt(request, previous)
                rooms = self.store.data["scopes"].get(scope, {})
                existing_active = next(
                    (room for room in rooms.values()
                     if _record_kind(room) == "turtle_soup" and room["status"] == "active"),
                    None,
                )
            level = normalize_content_level(getattr(self.plugin,
                "native_v2_turtle_soup_content_level", "normal"))
            scope_doc = request.get("context", {}).get("scope") if isinstance(request.get("context"), dict) else None
            session_id = str(scope_doc.get("session_id") or "") if isinstance(scope_doc, dict) else ""

        prepared_puzzle: SoupPuzzle | None = None
        if existing_active is None:
            prepared_puzzle = await self._prepare_turtle_puzzle(scope, level, session_id=session_id)

        async with self._async_lock:
            self._request(request)
            if not getattr(self.plugin, "native_v2_turtle_soup_enabled", False):
                raise NativeGameError("game_turtle_soup_disabled")
            self._checked(before_dispatch)
            with self.store.lock:
                previous = self.store.data["operations"].get(request["invocation_id"])
                if previous is not None:
                    if previous["identity"] != identity or previous["scope"] != scope:
                        raise NativeGameError("game_original_invocation_mismatch")
                    return self._receipt(request, previous)
                draft = copy.deepcopy(self.store.data)
                operation = {"id": "game-op-" + uuid.uuid4().hex, "scope": scope,
                             "identity": identity, "state": "succeeded", "effect": "committed", "result": None}
                try:
                    operation["result"] = {"game": self._apply(draft, scope, request, prepared_puzzle=prepared_puzzle)}
                except NativeGameError as exc:
                    draft = copy.deepcopy(self.store.data)
                    draft["scopes"].setdefault(scope, {})
                    operation.update(state="failed", effect="not_committed", error=exc.code)
                draft["operations"][request["invocation_id"]] = operation
                self._checked(before_dispatch)
                self.store.commit(draft)
                return self._receipt(request, operation)

    async def execute(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        # Release _async_lock across external LLM calls; keep store commits serialized.
        self._request(request)
        if request["action"] == TURTLE_CREATE:
            result = await self._execute_turtle_create(request, before_dispatch=before_dispatch)
        else:
            async with self._async_lock:
                self._request(request)
                if request["action"] in XIANGQI_ACTIONS:
                    result = await self._execute_xiangqi(request, before_dispatch=before_dispatch)
                else:
                    result = self._execute_sync(request, before_dispatch=before_dispatch)
        return await self._with_room_access(request, result, before_dispatch=before_dispatch)

    async def _with_room_access(self, request, result, *, before_dispatch):
        room_access = getattr(self.plugin, "native_game_room_access", None)
        if (request["action"] not in CREATE_ACTIONS or result["state"] != "succeeded"
                or not callable(room_access)):
            return result
        self._checked(before_dispatch)
        game = result["result"]["game"]
        try:
            access = await room_access(game["game_id"], before_dispatch=before_dispatch)
        except Exception:
            # Access failure does not undo a confirmed game commit. Lookup can
            # recover its browser entry without replaying the game operation.
            access = {"state": "unavailable", "reason": "game_room_access_unavailable"}
        self._checked(before_dispatch)
        result = copy.deepcopy(result)
        result["result"]["room_access"] = access
        if access.get("state") == "ready":
            result["result"]["room_url"] = access["url"]
        return result

    async def lookup(self, request: dict[str, Any], *, before_dispatch) -> dict[str, Any]:
        scope, _args = self._request(request)
        self._checked(before_dispatch)
        with self.store.lock:
            operation = self.store.data["operations"].get(request["invocation_id"])
            if (operation is None or operation["scope"] != scope
                    or operation["identity"] != _identity(request)
                    or (request.get("operation_id") and request["operation_id"] != operation["id"])):
                raise NativeGameError("game_operation_not_found")
            result = self._receipt(request, operation)
        return await self._with_room_access(request, result, before_dispatch=before_dispatch)

    @staticmethod
    def _web_ticket_key(ticket: str) -> str:
        return hashlib.sha256(ticket.encode("ascii", errors="ignore")).hexdigest()

    def conversation_snapshot(self, context: dict[str, Any], *, authorized_actions,
                              now: float, result_ttl: float, before_read) -> list[dict[str, Any]]:
        """Read this admitted chat's ledger without a model, engine or write."""
        if self.closed:
            return []
        self._checked(before_read)
        kinds = {SNAPSHOT_ACTIONS[action] for action in authorized_actions if action in SNAPSHOT_ACTIONS
                 and getattr(self.plugin, f"native_v2_{SNAPSHOT_ACTIONS[action]}_enabled", False)}
        with self.store.lock:
            rooms = self.store.data["scopes"].get(_hash(_context(context)), {})
            records = conversation_records(rooms, kinds, now=now, result_ttl=result_ttl)
            facts = [public_game_fact(room, _view(room) if _record_kind(room) != "xiangqi" else None)
                     for room in records]
            self._checked(before_read)
            return facts

    def list_web_games(self) -> list[dict[str, Any]]:
        """Return a minimal, identity-free inventory for the authenticated manager page."""
        if self.closed:
            return []
        with self.store.lock:
            games = []
            for scope, rooms in self.store.data["scopes"].items():
                for room in rooms.values():
                    kind = _record_kind(room)
                    if (room.get("status") != "active"
                            or not getattr(self.plugin, f"native_v2_{kind}_enabled", False)):
                        continue
                    games.append({"game_id": room["game_id"], "game_type": kind,
                                  "difficulty": room.get("difficulty", "normal"),
                                  "move_count": len(room.get("moves", room.get("events", room.get("guesses", []))))})
            return sorted(games, key=lambda item: item["game_id"])

    def _issue_web_ticket(self, scope: str, game_id: str, *, role: str = "drawer") -> str:
        if role not in {"drawer", "guesser"}:
            raise NativeGameError("invalid_game_arguments")
        now = time.time()
        self._web_sessions = {key: session for key, session in self._web_sessions.items()
                              if session["expires_at"] > now}
        ticket = secrets.token_urlsafe(32)
        self._web_sessions[self._web_ticket_key(ticket)] = {
            "scope": scope, "game_id": game_id, "role": role,
            "expires_at": now + WEB_TICKET_TTL_SECONDS,
        }
        if len(self._web_sessions) > WEB_TICKET_LIMIT:
            oldest = sorted(self._web_sessions.items(), key=lambda item: item[1]["expires_at"])
            for key, _session in oldest[:len(self._web_sessions) - WEB_TICKET_LIMIT]:
                self._web_sessions.pop(key, None)
        return ticket

    def issue_web_ticket(self, game_id: str, *, role: str = "drawer") -> str:
        if self.closed or type(game_id) is not str or not game_id:
            raise NativeGameError("game_not_found")
        if role not in {"drawer", "guesser"}:
            raise NativeGameError("invalid_game_arguments")
        with self.store.lock:
            for scope, rooms in self.store.data["scopes"].items():
                room = rooms.get(game_id)
                if room is None or room.get("status") != "active":
                    continue
                if not getattr(self.plugin, f"native_v2_{_record_kind(room)}_enabled", False):
                    raise NativeGameError("game_disabled")
                return self._issue_web_ticket(scope, game_id, role=role)
        raise NativeGameError("game_not_found")

    def _web_session(self, ticket: str) -> dict[str, Any]:
        if self.closed or type(ticket) is not str or not 24 <= len(ticket) <= 80:
            raise NativeGameError("game_web_link_invalid")
        key = self._web_ticket_key(ticket)
        session = self._web_sessions.get(key)
        if session is None or session["expires_at"] <= time.time():
            self._web_sessions.pop(key, None)
            raise NativeGameError("game_web_link_expired")
        return session

    def _web_game(self, session: dict[str, Any]) -> dict[str, Any]:
        room = self.store.data["scopes"].get(session["scope"], {}).get(session["game_id"])
        if room is None:
            raise NativeGameError("game_not_found")
        kind = _record_kind(room)
        if not getattr(self.plugin, f"native_v2_{kind}_enabled", False):
            raise NativeGameError(f"game_{kind}_disabled")
        return room

    async def web_state(self, ticket: str) -> dict[str, Any]:
        terminal_event: tuple[str, str, dict[str, Any]] | None = None
        async with self._async_lock:
            with self.store.lock:
                session = dict(self._web_session(ticket))
                room = copy.deepcopy(self._web_game(session))
                role = str(session.get("role") or "drawer")
                if _record_kind(room) == "draw_guess" and room.get("status") == "active":
                    draw_game = _draw_game(room)
                    if time.time() >= draw_game.deadline:
                        draft = copy.deepcopy(self.store.data)
                        live_room = draft["scopes"][session["scope"]][session["game_id"]]
                        live_room["timed_out"] = True
                        live_room["status"] = "finished"
                        live_room["outcome"] = "bot"
                        self.store.commit(draft)
                        room = copy.deepcopy(live_room)
                        terminal_event = (
                            session["scope"],
                            session["game_id"],
                            _view(room, viewer="guesser", include_strokes=False),
                        )
            if _record_kind(room) == "xiangqi":
                try:
                    game = await self._xiangqi_game(room)
                except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
                    raise NativeGameError("game_xiangqi_engine_unavailable") from exc
                result = xiangqi_view(room, game)
            else:
                result = _view(room, viewer=role, include_strokes=True)
        if terminal_event is not None:
            await self._notify_web_terminal(*terminal_event)
        return {"game": result, "expires_at": session["expires_at"]}

    @staticmethod
    def _web_action_request(kind: str, game_id: str, payload: Any, *, role: str = "drawer") -> dict[str, Any]:
        if type(payload) is not dict or type(payload.get("action")) is not str:
            raise NativeGameError("invalid_game_arguments")
        action = payload["action"]
        maps = {
            "tictactoe": {"move": MOVE, "interact": WEB_INTERACT, "reply": WEB_REPLY, "close": CLOSE},
            "gomoku": {"move": GOMOKU_MOVE, "interact": WEB_INTERACT, "reply": WEB_REPLY, "close": GOMOKU_CLOSE},
            "pig_dice": {"roll": PIG_ROLL, "hold": PIG_HOLD, "close": PIG_CLOSE},
            "blackjack": {"hit": BLACKJACK_HIT, "stand": BLACKJACK_STAND, "close": BLACKJACK_CLOSE},
            "xiangqi": {"move": XIANGQI_MOVE, "close": XIANGQI_CLOSE},
            "turtle_soup": {"hint": TURTLE_HINT, "give_up": TURTLE_GIVE_UP, "close": TURTLE_CLOSE},
            "draw_guess": {
                "strokes": WEB_DRAW_STROKES,
                "undo": WEB_DRAW_UNDO,
                "clear": WEB_DRAW_CLEAR,
                "bot_guess": WEB_DRAW_BOT_GUESS,
                "guess": DRAW_GUESS,
                "give_up": DRAW_GIVE_UP,
                "close": DRAW_CLOSE,
            },
        }
        native_action = maps.get(kind, {}).get(action)
        if native_action is None:
            raise NativeGameError("invalid_game_arguments")
        if kind == "draw_guess":
            if action in {"strokes", "undo", "clear", "bot_guess"} and role != "drawer":
                raise NativeGameError("game_drawer_only")
            if action == "guess" and role != "guesser":
                raise NativeGameError("game_guesser_only")
        arguments: dict[str, Any] = {"game_id": game_id}
        if action == "move" and kind in {"tictactoe", "gomoku"}:
            size = 15 if kind == "gomoku" else 3
            keys = set(payload)
            if (keys not in ({"action", "row", "column"}, {"action", "row", "column", "x", "y"})
                    or any(type(payload.get(key)) is not int or not 0 <= payload[key] < size
                           for key in ("row", "column"))):
                raise NativeGameError("invalid_game_arguments")
            arguments.update(row=payload["row"], column=payload["column"])
            if "x" in payload and (type(payload["x"]) not in (int, float)
                                    or isinstance(payload["x"], bool)
                                    or not math.isfinite(payload["x"])
                                    or not 0 <= payload["x"] <= 1
                                    or type(payload["y"]) not in (int, float)
                                    or isinstance(payload["y"], bool)
                                    or not math.isfinite(payload["y"])
                                    or not 0 <= payload["y"] <= 1):
                raise NativeGameError("invalid_game_arguments")
            if "x" in payload:
                arguments.update(x=float(payload["x"]), y=float(payload["y"]))
        elif action == "interact" and kind in BOARD_GAME_KINDS:
            source = payload.get("source")
            target = payload.get("target")
            if (set(payload) != {"action", "source", "target", "interaction"}
                    or type(source) is not dict or type(target) is not dict
                    or source.get("kind") not in {"tray", "board"}
                    or set(target) != {"row", "column", "x", "y"}
                    or type(target["row"]) is not int
                    or type(target["column"]) is not int
                    or not 0 <= target["row"] < (15 if kind == "gomoku" else 3)
                    or not 0 <= target["column"] < (15 if kind == "gomoku" else 3)
                    or type(target["x"]) not in (int, float) or isinstance(target["x"], bool)
                    or type(target["y"]) not in (int, float) or isinstance(target["y"], bool)
                    or not math.isfinite(target["x"]) or not math.isfinite(target["y"])
                    or not 0 <= target["x"] <= 1 or not 0 <= target["y"] <= 1
                    or payload["interaction"] not in {"click", "drag"}):
                raise NativeGameError("invalid_game_arguments")
            source_kind = source["kind"]
            if source_kind == "tray":
                if (set(source) != {"kind", "color"}
                        or type(source["color"]) is not int or source["color"] not in {1, 2}):
                    raise NativeGameError("invalid_game_arguments")
                arguments["source_color"] = source["color"]
            else:
                if (set(source) not in ({"kind", "piece_id"}, {"kind", "piece_id", "color"})
                        or type(source.get("piece_id")) is not str
                        or not 1 <= len(source["piece_id"]) <= 64
                        or ("color" in source and
                            (type(source["color"]) is not int or source["color"] not in {1, 2}))):
                    raise NativeGameError("invalid_game_arguments")
                arguments["source_piece_id"] = source["piece_id"]
                arguments["source_color"] = source.get("color")
            arguments.update(source_kind=source_kind, row=target["row"], column=target["column"],
                             x=float(target["x"]), y=float(target["y"]),
                             interaction=payload["interaction"])
        elif action == "roll" and kind == "pig_dice":
            if (set(payload) not in ({"action"}, {"action", "method"})
                    or payload.get("method", "table") not in {"table", "cup"}):
                raise NativeGameError("invalid_game_arguments")
            if "method" in payload:
                arguments["method"] = payload["method"]
        elif action == "move" and kind == "xiangqi":
            coordinates = {"from_row": 10, "from_column": 9, "to_row": 10, "to_column": 9}
            if (set(payload) != {"action", *coordinates}
                    or any(type(payload.get(key)) is not int or not 0 <= payload[key] < limit
                           for key, limit in coordinates.items())):
                raise NativeGameError("invalid_game_arguments")
            arguments.update({key: payload[key] for key in coordinates})
        elif action == "strokes" and kind == "draw_guess":
            if set(payload) != {"action", "strokes"}:
                raise NativeGameError("invalid_game_arguments")
            try:
                arguments["strokes"] = normalize_strokes(payload.get("strokes"))
            except ValueError as exc:
                raise NativeGameError("invalid_game_arguments") from exc
        elif action == "bot_guess" and kind == "draw_guess":
            image_data_url = payload.get("image_data_url")
            if (set(payload) != {"action", "image_data_url"}
                    or type(image_data_url) is not str
                    or not image_data_url.strip()
                    or len(image_data_url) > 700_000):
                raise NativeGameError("invalid_game_arguments")
            arguments["image_data_url"] = image_data_url.strip()
        elif action == "guess" and kind == "draw_guess":
            if (set(payload) != {"action", "guess"} or type(payload.get("guess")) is not str
                    or not payload["guess"].strip() or len(payload["guess"]) > 80):
                raise NativeGameError("invalid_game_arguments")
            arguments["guess"] = payload["guess"]
        elif set(payload) != {"action"}:
            raise NativeGameError("invalid_game_arguments")
        return {"action": native_action, "arguments": arguments,
                "invocation_id": "web-" + uuid.uuid4().hex}

    async def web_action(self, ticket: str, payload: Any) -> dict[str, Any]:
        terminal_event: tuple[str, str, dict[str, Any]] | None = None
        bot_guess_job: tuple[dict[str, Any], str, DrawGuessGame, Any, str, str, str, str] | None = None
        async with self._async_lock:
            with self.store.lock:
                session = dict(self._web_session(ticket))
                room = self._web_game(session)
                kind = _record_kind(room)
                role = str(session.get("role") or "drawer")
                was_active = room.get("status") == "active"
                request = self._web_action_request(kind, session["game_id"], payload, role=role)
                room_snapshot = copy.deepcopy(room)
                draft = copy.deepcopy(self.store.data)
            if request["action"] == WEB_DRAW_BOT_GUESS:
                if not was_active:
                    raise NativeGameError("game_not_active")
                draw_game = _draw_game(room_snapshot)
                if time.time() >= draw_game.deadline:
                    draft["scopes"][session["scope"]][session["game_id"]].update(
                        timed_out=True, status="finished", outcome="bot"
                    )
                    result = _view(
                        draft["scopes"][session["scope"]][session["game_id"]],
                        viewer=role,
                        include_strokes=True,
                    )
                else:
                    if not draw_game.strokes:
                        raise NativeGameError("game_drawing_empty")
                    guesser = getattr(self.plugin, "native_guess_drawing", None)
                    if not callable(guesser):
                        raise NativeGameError("game_vision_unavailable")
                    ctx = self._game_contexts.get(session["game_id"]) or self._scope_contexts.get(session["scope"])
                    user_id = str(getattr(getattr(getattr(ctx, "actor", None), "principal", None), "native_account_id", "") or "")
                    session_id = str(getattr(getattr(ctx, "scope", None), "session_id", "") or "")
                    bot_guess_job = (
                        session,
                        role,
                        draw_game,
                        guesser,
                        request["arguments"]["image_data_url"],
                        request["invocation_id"],
                        user_id,
                        session_id,
                    )
            elif request["action"] == XIANGQI_MOVE or request["action"] == XIANGQI_CLOSE:
                try:
                    room, game = await self._apply_xiangqi(draft, session["scope"], request)
                except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
                    raise NativeGameError("game_xiangqi_engine_unavailable") from exc
                result = xiangqi_view(room, game)
            else:
                result = self._apply(
                    draft, session["scope"], request,
                    viewer=role, include_strokes=True,
                )
            if bot_guess_job is None:
                with self.store.lock:
                    self.store.commit(draft)
                    if kind == "xiangqi":
                        self._cache_xiangqi(room, game)
                    if was_active and result.get("status") == "finished":
                        committed = draft["scopes"][session["scope"]][session["game_id"]]
                        public_result = (
                            result if kind == "xiangqi"
                            else _view(committed, viewer="guesser", include_strokes=False)
                        )
                        terminal_event = (session["scope"], session["game_id"], public_result)
        if bot_guess_job is not None:
            session, role, draw_game, guesser, image_data_url, invocation_id, user_id, session_id = bot_guess_job
            guess_text = await guesser(
                draw_game,
                image_data_url,
                user_id=user_id,
                session_id=session_id,
            )
            async with self._async_lock:
                with self.store.lock:
                    session = dict(self._web_session(ticket))
                    live_room = self._web_game(session)
                    if live_room.get("status") != "active":
                        raise NativeGameError("game_not_active")
                    fresh_draft = copy.deepcopy(self.store.data)
                    guess_request = {
                        "action": DRAW_GUESS,
                        "arguments": {"game_id": session["game_id"], "guess": guess_text},
                        "invocation_id": invocation_id,
                    }
                    result = self._apply(
                        fresh_draft, session["scope"], guess_request,
                        viewer=role, include_strokes=True,
                    )
                    self.store.commit(fresh_draft)
                    if result.get("status") == "finished":
                        committed = fresh_draft["scopes"][session["scope"]][session["game_id"]]
                        terminal_event = (
                            session["scope"],
                            session["game_id"],
                            _view(committed, viewer="guesser", include_strokes=False),
                        )
        if terminal_event is not None:
            await self._notify_web_terminal(*terminal_event)
        return {"game": result, "expires_at": session["expires_at"]}

    async def create_mobile_game(self, owner_id: str, game_type: str,
                                 difficulty: str = "normal") -> dict[str, Any]:
        """Create a native game for an authenticated V1 mobile-gateway owner."""
        owner = str(owner_id or "").strip()[:120]
        kind = str(game_type or "").strip().lower()
        if not owner or kind not in {"tictactoe", "gomoku", "pig_dice", "blackjack",
                                     "xiangqi", "turtle_soup", "draw_guess"}:
            raise NativeGameError("invalid_game_arguments")
        if difficulty not in {"easy", "normal", "hard"}:
            raise NativeGameError("invalid_game_arguments")
        scope = _hash({"provider": PROVIDER_ID, "channel": "mobile-gateway-v1", "owner": owner})
        create_actions = {"tictactoe": CREATE, "gomoku": GOMOKU_CREATE, "pig_dice": PIG_CREATE,
                          "blackjack": BLACKJACK_CREATE, "xiangqi": XIANGQI_CREATE,
                          "turtle_soup": TURTLE_CREATE, "draw_guess": DRAW_CREATE}
        args: dict[str, Any] = {} if kind == "turtle_soup" else {"difficulty": difficulty}
        if kind == "xiangqi":
            args["human_side"] = XIANGQI_RED
        request = {"action": create_actions[kind], "arguments": args,
                   "invocation_id": "mobile-" + uuid.uuid4().hex}
        prepared_puzzle: SoupPuzzle | None = None
        if kind == "turtle_soup":
            async with self._async_lock:
                if not getattr(self.plugin, "native_v2_turtle_soup_enabled", False):
                    raise NativeGameError("game_turtle_soup_disabled")
                with self.store.lock:
                    existing_active = next(
                        (item for item in self.store.data["scopes"].get(scope, {}).values()
                         if _record_kind(item) == "turtle_soup" and item["status"] == "active"),
                        None,
                    )
                level = normalize_content_level(getattr(self.plugin,
                    "native_v2_turtle_soup_content_level", "normal"))
            if existing_active is None:
                prepared_puzzle = await self._prepare_turtle_puzzle(scope, level, difficulty=difficulty)
        async with self._async_lock:
            if not getattr(self.plugin, f"native_v2_{kind}_enabled", False):
                raise NativeGameError(f"game_{kind}_disabled")
            with self.store.lock:
                draft = copy.deepcopy(self.store.data)
            if kind == "xiangqi":
                try:
                    room, game = await self._apply_xiangqi(draft, scope, request)
                except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
                    raise NativeGameError("game_xiangqi_engine_unavailable") from exc
                result = xiangqi_view(room, game)
            else:
                result = self._apply(draft, scope, request, prepared_puzzle=prepared_puzzle)
                room = draft["scopes"][scope][result["game_id"]]
            with self.store.lock:
                self.store.commit(draft)
                if kind == "xiangqi":
                    self._cache_xiangqi(room, game)
                ticket = self._issue_web_ticket(scope, room["game_id"])
            return {"ticket": ticket, "game": result}

    async def close(self) -> None:
        self.closed = True
        self._xiangqi_cache.clear()
        self._web_sessions.clear()
        self._game_contexts.clear()
        self._scope_contexts.clear()
        self._web_terminal_callback = None
