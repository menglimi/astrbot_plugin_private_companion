# -*- coding: utf-8 -*-
"""Durable, failure-contained delivery queue for Bot Personal archives.

The companion plugin owns the local write.  A remote MemoryCompanion Bridge is
only a delivery target, so a missing or failing bridge cannot interrupt chat,
agenda maintenance, or proactive messaging.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable, Mapping

from .bot_personal_contract import BOT_PERSONAL_CANONICAL_SCHEMA_VERSION
from .bot_personal_dto import BotPersonalArchiveDTO, build_bot_personal_dto


OUTBOX_STATES = frozenset({"pending", "retry", "sent", "deduplicated", "dead_letter", "invalid"})
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: Any, fallback: float | None = None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return float(fallback if fallback is not None else 0.0)


def _fingerprint(envelope: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(envelope), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutboxResult:
    ok: bool
    state: str
    idempotency_key: str
    record_id: str = ""
    deduplicated: bool = False
    version: int = 0
    error_code: str | None = None
    attempts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state,
            "idempotency_key": self.idempotency_key,
            "record_id": self.record_id,
            "deduplicated": self.deduplicated,
            "version": self.version,
            "error_code": self.error_code,
            "attempts": self.attempts,
        }


class BotPersonalOutbox:
    """Persisted queue backed by ``plugin.data['bot_personal_outbox']``.

    ``clock`` returns wall-clock seconds and is injectable for deterministic
    tests.  ``sender`` is called only by :meth:`drain`; it should return the
    structured Bridge result and must not raise for ordinary remote failures.
    """

    def __init__(
        self,
        data: dict[str, Any],
        *,
        save: Callable[[], Any] | None = None,
        clock: Callable[[], float] | None = None,
        max_attempts: int = 5,
        base_backoff_seconds: float = 30.0,
        max_backoff_seconds: float = 3600.0,
        background_task: Callable[[Awaitable[Any], str], Any] | None = None,
    ) -> None:
        self.data = data
        self.save = save
        self.clock = clock or __import__("time").time
        self.max_attempts = max(1, int(max_attempts or 5))
        self.base_backoff_seconds = max(0.0, float(base_backoff_seconds or 30.0))
        self.max_backoff_seconds = max(self.base_backoff_seconds, float(max_backoff_seconds or 3600.0))
        self.background_task = background_task
        self._persist_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        entries = self.data.setdefault("bot_personal_outbox", [])
        if not isinstance(entries, list):
            self.data["bot_personal_outbox"] = []

    @property
    def entries(self) -> list[dict[str, Any]]:
        entries = self.data.get("bot_personal_outbox")
        if not isinstance(entries, list):
            entries = []
            self.data["bot_personal_outbox"] = entries
        return [item for item in entries if isinstance(item, dict)]

    def _replace_entries(self, entries: list[dict[str, Any]]) -> None:
        self.data["bot_personal_outbox"] = entries

    def _persist(self) -> None:
        if not callable(self.save):
            return
        try:
            result = self.save()
            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                if callable(self.background_task):
                    try:
                        task = self.background_task(result, "bot_personal_outbox_save")
                    except Exception:
                        closer = getattr(result, "close", None)
                        if callable(closer):
                            closer()
                        raise
                    if task is None:
                        closer = getattr(result, "close", None)
                        if callable(closer):
                            closer()
                    return
                try:
                    task = asyncio.create_task(result)
                except RuntimeError:
                    closer = getattr(result, "close", None)
                    if callable(closer):
                        closer()
                    return
                self._persist_tasks.add(task)
                task.add_done_callback(self._collect_persist_task)
        except Exception:
            # The in-memory queue remains authoritative for this process; the
            # next normal store save can still persist the already-mutated data.
            return

    def _collect_persist_task(self, task: asyncio.Task) -> None:
        self._persist_tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning("Bot Personal outbox save task failed: %s", error, exc_info=error)

    @staticmethod
    def _key(
        memory_type: str,
        idempotency_key: str,
        *,
        owner_bot_id: str = "",
        persona_id: str = "",
    ) -> str:
        base = f"{str(memory_type or '').strip()}::{str(idempotency_key or '').strip()}"
        owner = str(owner_bot_id or "").strip()
        persona = str(persona_id or "").strip()
        return f"{owner}::{persona}::{base}" if owner or persona else base

    def _find(
        self,
        entries: list[dict[str, Any]],
        memory_type: str,
        key: str,
        *,
        owner_bot_id: str = "",
        persona_id: str = "",
    ) -> dict[str, Any] | None:
        composite = self._key(
            memory_type,
            key,
            owner_bot_id=owner_bot_id,
            persona_id=persona_id,
        )
        for entry in entries:
            envelope = entry.get("envelope") if isinstance(entry.get("envelope"), dict) else {}
            if self._key(
                entry.get("memory_type"),
                entry.get("idempotency_key"),
                owner_bot_id=envelope.get("owner_bot_id", ""),
                persona_id=envelope.get("persona_id", ""),
            ) == composite:
                return entry
        return None

    def _normalise_sender_result(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"ok": False, "state": "retry", "error_code": "invalid_bridge_response"}
        normalized = dict(result)
        normalized["ok"] = bool(result.get("ok"))
        normalized["deduplicated"] = bool(result.get("deduplicated"))
        normalized["version"] = int(result.get("version") or 0)
        normalized["state"] = str(result.get("state") or ("sent" if normalized["ok"] else "retry"))
        return normalized

    def _backoff(self, attempts: int) -> float:
        exponent = max(0, int(attempts) - 1)
        return min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** exponent))

    async def enqueue(
        self,
        *,
        memory_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        occurred_at: str,
        now: float | None = None,
        version: int = 1,
        source_refs: list[str] | None = None,
        sender: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate, locally record, then optionally attempt immediate delivery."""
        try:
            dto = build_bot_personal_dto(
                memory_type=memory_type,
                payload=payload,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
                version=version,
                **kwargs,
            )
        except Exception as exc:
            return OutboxResult(
                ok=False,
                state="invalid",
                idempotency_key=str(idempotency_key or ""),
                error_code=getattr(exc, "error_code", "invalid"),
            ).as_dict()

        envelope = dto.envelope()
        if source_refs:
            envelope["source_refs"] = list(dict.fromkeys(str(item).strip()[:240] for item in source_refs if str(item).strip()))
        envelope_fingerprint = _fingerprint(envelope)
        current = float(self.clock() if now is None else now)
        owner_bot_id = (
            dto.owner_bot_id
            if dto.canonical_schema_version >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION
            else ""
        )
        persona_id = (
            dto.persona_id
            if dto.canonical_schema_version >= BOT_PERSONAL_CANONICAL_SCHEMA_VERSION
            else ""
        )
        scoped_key = self._key(
            dto.memory_type,
            dto.idempotency_key,
            owner_bot_id=owner_bot_id,
            persona_id=persona_id,
        )
        async with self._lock:
            entries = self.entries
            existing = self._find(
                entries,
                dto.memory_type,
                dto.idempotency_key,
                owner_bot_id=owner_bot_id,
                persona_id=persona_id,
            )
            if existing is not None:
                old_version = int(existing.get("version") or 0)
                old_fingerprint = str(existing.get("payload_fingerprint") or "")
                if old_version > dto.version:
                    return OutboxResult(
                        ok=False,
                        state="stale_version",
                        idempotency_key=dto.idempotency_key,
                        record_id=str(existing.get("record_id") or dto.record_id),
                        version=old_version,
                        error_code="stale_version",
                        attempts=int(existing.get("attempts") or 0),
                    ).as_dict()
                if old_version == dto.version and old_fingerprint == envelope_fingerprint:
                    return OutboxResult(
                        ok=existing.get("state") in {"sent", "deduplicated"},
                        state=str(existing.get("state") or "pending"),
                        idempotency_key=dto.idempotency_key,
                        record_id=str(existing.get("record_id") or dto.record_id),
                        deduplicated=True,
                        version=old_version,
                        error_code=existing.get("last_error"),
                        attempts=int(existing.get("attempts") or 0),
                    ).as_dict()
                if old_version == dto.version and old_fingerprint != envelope_fingerprint:
                    return OutboxResult(
                        ok=False,
                        state="version_conflict",
                        idempotency_key=dto.idempotency_key,
                        record_id=str(existing.get("record_id") or dto.record_id),
                        version=old_version,
                        error_code="version_conflict",
                        attempts=int(existing.get("attempts") or 0),
                    ).as_dict()
                existing.update({
                    "envelope": deepcopy(envelope),
                    "payload_fingerprint": envelope_fingerprint,
                    "record_id": dto.record_id,
                    "version": dto.version,
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": current,
                    "last_error": "",
                    "updated_at": _now_iso(),
                    "sent_at": "",
                    "dead_letter_at": "",
                    "remote_record_id": "",
                    "remote_version": 0,
                })
                entry = existing
            else:
                entry = {
                    "outbox_id": f"outbox_{hashlib.sha1(scoped_key.encode()).hexdigest()[:20]}",
                    "memory_type": dto.memory_type,
                    "idempotency_key": dto.idempotency_key,
                    "record_id": dto.record_id,
                    "version": dto.version,
                    "envelope": deepcopy(envelope),
                    "payload_fingerprint": envelope_fingerprint,
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": current,
                    "last_error": "",
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                    "sent_at": "",
                    "dead_letter_at": "",
                }
                entries.append(entry)
            self._replace_entries(entries)
            self._persist()

        result = OutboxResult(
            ok=False,
            state="pending",
            idempotency_key=dto.idempotency_key,
            record_id=dto.record_id,
            version=dto.version,
        ).as_dict()
        if callable(sender):
            drained = await self.drain(sender, now=current, limit=1)
            if drained:
                return drained[0]
        return result

    async def drain(
        self,
        sender: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        now: float | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        """Try due entries once; retries are scheduled without sleeping."""
        current = float(self.clock() if now is None else now)
        results: list[dict[str, Any]] = []
        for _ in range(max(0, int(limit or 0))):
            async with self._lock:
                due = [
                    item for item in self.entries
                    if item.get("state") in {"pending", "retry"}
                    and _timestamp(item.get("next_attempt_at"), current) <= current
                ]
                if not due:
                    break
                entry = due[0]
                entry["attempts"] = int(entry.get("attempts") or 0) + 1
                entry["updated_at"] = _now_iso()
                envelope = deepcopy(entry.get("envelope") or {})
                attempt = int(entry["attempts"])
                sent_record_id = str(entry.get("record_id") or "")
                sent_version = int(entry.get("version") or 0)
                sent_fingerprint = str(entry.get("payload_fingerprint") or "")
                self._persist()
            try:
                response = self._normalise_sender_result(await sender(envelope))
            except Exception as exc:
                response = {"ok": False, "state": "retry", "error_code": type(exc).__name__}
            async with self._lock:
                current_entry = self._find(
                    self.entries,
                    entry.get("memory_type", ""),
                    entry.get("idempotency_key", ""),
                    owner_bot_id=envelope.get("owner_bot_id", ""),
                    persona_id=envelope.get("persona_id", ""),
                )
                if current_entry is None:
                    continue
                if (
                    str(current_entry.get("record_id") or "") != sent_record_id
                    or int(current_entry.get("version") or 0) != sent_version
                    or str(current_entry.get("payload_fingerprint") or "")
                    != sent_fingerprint
                ):
                    # A newer revision replaced this logical outbox entry while
                    # the old request was in flight. Its response must not
                    # mutate the newer pending delivery.
                    results.append({
                        "ok": False,
                        "state": "superseded",
                        "idempotency_key": current_entry.get("idempotency_key", ""),
                        "record_id": current_entry.get("record_id", ""),
                        "deduplicated": False,
                        "version": int(current_entry.get("version") or 0),
                        "error_code": "superseded_in_flight_revision",
                        "attempts": attempt,
                    })
                    continue
                current_entry["updated_at"] = _now_iso()
                current_entry["last_error"] = str(response.get("error_code") or "")[:120]
                current_entry["remote_record_id"] = str(response.get("record_id") or "")[:160]
                current_entry["remote_version"] = int(response.get("version") or 0)
                if response.get("ok"):
                    current_entry["state"] = "deduplicated" if response.get("deduplicated") else "sent"
                    current_entry["sent_at"] = _now_iso()
                    current_entry["next_attempt_at"] = 0
                elif response.get("state") in {"invalid", "version_conflict", "stale_version"}:
                    current_entry["state"] = "dead_letter" if response.get("state") != "invalid" else "invalid"
                    current_entry["dead_letter_at"] = _now_iso()
                    current_entry["next_attempt_at"] = 0
                elif attempt >= self.max_attempts:
                    current_entry["state"] = "dead_letter"
                    current_entry["dead_letter_at"] = _now_iso()
                    current_entry["next_attempt_at"] = 0
                else:
                    current_entry["state"] = "retry"
                    current_entry["next_attempt_at"] = current + self._backoff(attempt)
                self._persist()
                results.append({
                    "ok": bool(response.get("ok")),
                    "state": current_entry["state"],
                    "idempotency_key": current_entry.get("idempotency_key", ""),
                    "record_id": current_entry.get("remote_record_id") or current_entry.get("record_id", ""),
                    "deduplicated": bool(response.get("deduplicated")),
                    "version": int(response.get("version") or current_entry.get("version") or 0),
                    "error_code": current_entry.get("last_error") or None,
                    "attempts": attempt,
                })
        return results

    async def retry_dead_letter(self, idempotency_key: str, *, now: float | None = None) -> dict[str, Any]:
        current = float(self.clock() if now is None else now)
        async with self._lock:
            for entry in self.entries:
                if entry.get("idempotency_key") != idempotency_key:
                    continue
                if entry.get("state") != "dead_letter":
                    return {"ok": False, "state": str(entry.get("state") or "pending"), "idempotency_key": idempotency_key}
                entry.update({"state": "retry", "next_attempt_at": current, "last_error": "", "dead_letter_at": "", "updated_at": _now_iso()})
                self._persist()
                return {"ok": True, "state": "retry", "idempotency_key": idempotency_key, "attempts": int(entry.get("attempts") or 0)}
        return {"ok": False, "state": "missing", "idempotency_key": idempotency_key, "error_code": "not_found"}

    def status(self) -> dict[str, Any]:
        entries = self.entries
        counts = {state: 0 for state in OUTBOX_STATES}
        for entry in entries:
            state = str(entry.get("state") or "pending")
            counts[state] = counts.get(state, 0) + 1
        return {"total": len(entries), "counts": counts, "pending": counts.get("pending", 0), "retry": counts.get("retry", 0), "sent": counts.get("sent", 0) + counts.get("deduplicated", 0), "dead_letter": counts.get("dead_letter", 0)}


__all__ = ["BotPersonalOutbox", "OutboxResult", "OUTBOX_STATES"]
