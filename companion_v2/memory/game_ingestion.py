"""Project confirmed Game terminal receipts into private, sourced experiences."""

import json

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.conversation.native_tool_results import operation_rows
from companion_v2.sources.models import SourceRecord

from .models import Evidence, owner_key


GAME_ACTIONS = {"game.tictactoe.move": ("tictactoe", "井字棋"),
                "game.gomoku.move": ("gomoku", "五子棋"),
                "game.pig_dice.roll": ("pig_dice", "掷骰竞分"),
                "game.pig_dice.hold": ("pig_dice", "掷骰竞分"),
                "game.blackjack.create": ("blackjack", "二十一点"),
                "game.blackjack.hit": ("blackjack", "二十一点"),
                "game.blackjack.stand": ("blackjack", "二十一点"),
                "game.turtle_soup.answer": ("turtle_soup", "海龟汤"),
                "game.turtle_soup.give_up": ("turtle_soup", "海龟汤"),
                "game.draw_guess.guess": ("draw_guess", "你画我猜"),
                "game.draw_guess.give_up": ("draw_guess", "你画我猜"),
                "game.xiangqi.create": ("xiangqi", "中国象棋"),
                "game.xiangqi.move": ("xiangqi", "中国象棋")}
CURSOR_PREFIX = "memory.game-ingestion.v1:"
TERMINAL_UNAVAILABLE = frozenset({"source_revoked", "source_expired", "source_dependency_unavailable",
    "source_dependency_expired", "memory_evidence_forgotten", "tool_owner_unavailable"})
OUTCOMES = {"player": "玩家获胜", "bot": "角色获胜", "draw": "平局"}
COOPERATIVE_OUTCOMES = {
    "turtle_soup": {"player": "玩家解开汤底", "bot": "玩家放弃"},
    "draw_guess": {"player": "合作猜中", "bot": "本轮未猜中"},
}
# The provider reports ``active | finished | closed``. Only a *finished* session carries
# a result, and only a result is material for an experience: a session that was started
# and later closed is remembered by the role's own continuity, not as a Game outcome, so
# this producer deliberately writes nothing for it. Completion, pausing and archiving are
# understood separately.
CLOSE_ACTIONS = frozenset({"game.tictactoe.close", "game.gomoku.close", "game.pig_dice.close",
                           "game.blackjack.close", "game.xiangqi.close", "game.turtle_soup.close",
                           "game.draw_guess.close"})


GAME_TYPE_LABELS = {
    "tictactoe": "井字棋",
    "gomoku": "五子棋",
    "pig_dice": "掷骰竞分",
    "blackjack": "二十一点",
    "xiangqi": "中国象棋",
    "turtle_soup": "海龟汤",
    "draw_guess": "你画我猜",
}
ALLOWED_RESULT_KEYS = frozenset({"game", "room_access", "room_url"})


def terminal_result(game):
    """The finished result of one Game view, or None when it is not a finished session.

    ``active`` and ``closed`` both return None: neither has an outcome, and an abandoned
    session must not be recorded as if it had been played to an end.
    """
    if type(game) is not dict or game.get("status") != "finished":
        return None
    outcome = game.get("outcome")
    return outcome if type(outcome) is str and outcome in OUTCOMES else None


class MemoryGameIngestion:
    page_size = 16

    def __init__(self, memory, external, *, require_running=None):
        if external.tasks is not memory.tasks or external.messages is None:
            raise ValueError("game memory requires the attached original tool ledger")
        self.memory, self.external = memory, external
        self.tasks, self.runtime = memory.tasks, memory.runtime
        self.require_running = require_running
        self.closed = False
        self._last_advance = {}
        self._binding_offset = 0

    def _active(self):
        if self.closed:
            raise TaskError("memory_game_closed", "game memory producer is stopped")
        if self.require_running:
            self.require_running()
        require(self.tasks.check_generation())

    @staticmethod
    def _identity(context, invocation_id):
        return "game-experience-" + digest([owner_key(context), context.scope, invocation_id])

    @outcome
    def record(self, context, invocation_id):
        """Use only the settled child task and its governed native original."""
        with self.runtime.transaction():
            self._active()
            self.memory.writer.access.check(context, "memory.propose")
            row = self.external._row(invocation_id)
            if row["action"] not in GAME_ACTIONS or row["state"] != "succeeded":
                return {"state": "not_completed", "view_id": None}
            receipt, material = self.external.snapshot(row, context, self.tasks.now())
            if (receipt["state"] != "succeeded" or receipt["effect"] != "committed"
                    or receipt["operation_id"] is None or material is None or material.source is None):
                return {"state": "not_completed", "view_id": None}
            original = self.memory.writer.access.source(context, material.source, action="memory.propose")
            if original.payload != material.payload or material.source.owner != "game.native":
                raise TaskError("memory_game_source_changed", "game result differs from its original source")
            native = json.loads(original.payload).get("native")
            if (type(native) is not dict or native.get("operation_id") != receipt["operation_id"]
                    or native.get("receipt_ref") != receipt["receipt_ref"]
                    or type(native.get("result")) is not dict):
                raise TaskError("memory_game_receipt_changed", "game result differs from its confirmed receipt")
            result = native["result"]
            game = (result.get("game")
                    if "game" in result and set(result).issubset(ALLOWED_RESULT_KEYS)
                    else None)
            outcome_name = terminal_result(game)
            if outcome_name is None:
                return {"state": "not_completed", "view_id": None}
            game_id = game.get("game_id")
            game_type, game_label = GAME_ACTIONS[row["action"]]
            if (game.get("game_type") != game_type or type(game_id) is not str
                    or not 1 <= len(game_id) <= 128):
                raise TaskError("memory_game_receipt_changed", "game terminal result is invalid")
            label = COOPERATIVE_OUTCOMES.get(game_type, OUTCOMES).get(outcome_name)
            if label is None:
                raise TaskError("memory_game_receipt_changed", "game terminal result is invalid")
            statement = f"Game 执行端确认当前会话的{game_label}对局 {game_id} 已结束，结果为{label}。"
            evidence = Evidence(material.source, original.content_hash, 0, len(original.payload),
                digest(original.payload), context.role_binding.role.subject_id, "observation")
            identity = self._identity(context, invocation_id)
            return require(self.memory.experiences.save(context, view_id=identity, expected_revision=0,
                title=game_label + "终局：" + label,
                statements=({"text": statement, "evidence": (evidence,), "atoms": ()},),
                entities=(game_label, "游戏:" + game_id), occurred_at=material.source.issued_at,
                operation_key=identity))

    @staticmethod
    def _web_identity(context, game_id):
        return "game-experience-web-" + digest([owner_key(context), context.scope, game_id])

    @staticmethod
    def _sanitized_web_game(game):
        """Retain only public terminal facts for the governed web-completion source."""
        return {
            "game_id": game["game_id"],
            "game_type": game["game_type"],
            "status": game["status"],
            "outcome": game["outcome"],
        }

    @outcome
    def record_web_terminal(self, context, game):
        """Record a terminal result completed from the native browser room into governed memory."""
        with self.runtime.transaction() as connection:
            self._active()
            if getattr(getattr(context, "scope", None), "kind", None) is not ScopeKind.PRIVATE:
                return {"state": "not_completed", "view_id": None}
            self.memory.writer.access.check(context, "memory.propose")
            outcome_name = terminal_result(game)
            if outcome_name is None:
                return {"state": "not_completed", "view_id": None}
            game_id = game.get("game_id")
            game_type = game.get("game_type")
            game_label = GAME_TYPE_LABELS.get(game_type)
            if (game_label is None or type(game_id) is not str
                    or not 1 <= len(game_id) <= 128):
                raise TaskError("memory_game_receipt_changed", "game terminal result is invalid")
            label = COOPERATIVE_OUTCOMES.get(game_type, OUTCOMES).get(outcome_name)
            if label is None:
                raise TaskError("memory_game_receipt_changed", "game terminal result is invalid")
            identity = self._web_identity(context, game_id)
            existing = connection.execute(
                "SELECT view_id, revision FROM memory_views WHERE view_id = ? AND owner_key = ?",
                (identity, owner_key(context)),
            ).fetchone()
            if existing is not None:
                return {"view_id": existing["view_id"], "revision": existing["revision"], "state": "saved"}
            now = self.tasks.now()
            payload = encode({"native": {"source": "web", "result": {"game": self._sanitized_web_game(game)}}})
            source_id = "game.native:web-terminal:" + digest([owner_key(context), context.scope, game_id])
            source_ref = SourceRef(source_id, "game.native", context.scope, VersionRef(source_id, 1), now)
            source_record = SourceRecord(source_ref, context.audience, digest(payload), payload)
            published = require(self.memory.writer.sources.publish(source_record, observed_at=now))
            statement = f"Game 网页端确认当前会话的{game_label}对局 {game_id} 已结束，结果为{label}。"
            evidence = Evidence(
                published.reference, published.content_hash, 0, len(published.payload),
                digest(published.payload), context.role_binding.role.subject_id, "observation",
            )
            return require(self.memory.experiences.save(
                context, view_id=identity, expected_revision=0,
                title=game_label + "终局：" + label,
                statements=({"text": statement, "evidence": (evidence,), "atoms": ()},),
                entities=(game_label, "游戏:" + game_id), occurred_at=now,
                operation_key=identity,
            ))

    @outcome
    def advance(self, context):
        with self.runtime.transaction() as connection:
            self._active()
            self.memory.writer.access.check(context, "memory.propose")
            actions = tuple(GAME_ACTIONS)
            placeholders = ", ".join("?" for _ in actions)
            report = {"state": "ready", "examined": 0, "saved": 0, "skipped": 0,
                      "more": False, "blocked": []}
            for native in (False, True):
                key = CURSOR_PREFIX + ("native:" if native else "") + context.key
                saved = connection.execute("SELECT value FROM runtime_metadata WHERE key = ?", (key,)).fetchone()
                cursor = json.loads(saved["value"]) if saved else 0
                if type(cursor) is not int or cursor < 0:
                    raise TaskError("memory_game_cursor_invalid", "game receipt cursor is invalid")
                rows = operation_rows(self.runtime, context, actions, after=cursor, limit=self.page_size + 1) if native else (
                    connection.execute(f"""SELECT i.rowid AS scan_id, i.invocation_id, i.state
                        FROM extension_tool_invocations i JOIN conversation_batches b ON b.batch_id = i.batch_id
                        WHERE i.rowid > ? AND i.action IN ({placeholders}) AND b.origin_key = ?
                        ORDER BY i.rowid LIMIT ?""", (cursor, *actions, context.key, self.page_size + 1)).fetchall())
                report["more"] = report["more"] or len(rows) > self.page_size
                for row in rows[:self.page_size]:
                    report["examined"] += 1
                    if row["state"] in {"queued", "running"}:
                        report["state"], report["more"] = "blocked", True
                        report["blocked"].append({"invocation_id": row["invocation_id"], "code": "external_task_unresolved"})
                        break
                    if row["state"] == "succeeded":
                        result = self.record(context, row["invocation_id"])
                        if result.is_success:
                            report["saved" if result.unwrap()["view_id"] else "skipped"] += 1
                        elif result.failure.code in TERMINAL_UNAVAILABLE:
                            report["skipped"] += 1
                        else:
                            report["state"], report["more"] = "blocked", True
                            report["blocked"].append({"invocation_id": row["invocation_id"],
                                                      "code": result.failure.code})
                            break
                    else:
                        report["skipped"] += 1
                    cursor = row["scan_id"]
                connection.execute("""INSERT INTO runtime_metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (key, encode(cursor)))
            return report

    def has_due_bindings(self, bindings) -> bool:
        """Fast O(1) readiness probe so idle ticks skip worker-thread dispatch."""
        if self.closed:
            return False
        windows = tuple(bindings)
        if not windows:
            return bool(self._last_advance)
        now = self.tasks.now()
        start, count = self._binding_offset % len(windows), min(16, len(windows))
        for index in range(count):
            context = windows[(start + index) % len(windows)][0]
            if context.scope.kind is not ScopeKind.PRIVATE:
                continue
            previous = self._last_advance.get(context.key)
            if previous is None or not (0 <= (now - previous).total_seconds() < 30):
                return True
        return False

    @outcome
    def advance_bound(self, bindings):
        self._active()
        windows = tuple(bindings)
        report = {"considered": 0, "saved": 0, "blocked": []}
        if not windows:
            self._last_advance.clear()
            self._binding_offset = 0
            return report
        keys = {window[0].key for window in windows}
        self._last_advance = {key: at for key, at in tuple(self._last_advance.items()) if key in keys}
        now = self.tasks.now()
        start, count = self._binding_offset % len(windows), min(16, len(windows))
        self._binding_offset = (start + count) % len(windows)
        for index in range(count):
            context = windows[(start + index) % len(windows)][0]
            report["considered"] += 1
            if context.scope.kind is not ScopeKind.PRIVATE:
                continue
            previous = self._last_advance.get(context.key)
            if previous is not None and 0 <= (now - previous).total_seconds() < 30:
                continue
            self._last_advance[context.key] = now
            result = self.advance(context)
            if result.is_success:
                report["saved"] += result.unwrap()["saved"]
                report["blocked"].extend(result.unwrap()["blocked"])
            else:
                self._active()
                report["blocked"].append({"context_key": context.key, "code": result.failure.code})
        return report

    def close(self):
        self.closed = True
        self._last_advance.clear()
        self._binding_offset = 0
