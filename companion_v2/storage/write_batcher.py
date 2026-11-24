"""Coalesced write-behind micro-batching for non-critical SQLite writes.

Rapid telemetry, audit, or diagnostic updates can trigger dozens of separate
single-row transactions (and WAL fsyncs) per second on low-IOPS cloud disks.
``WriteBehindBatcher`` accumulates callbacks for a short window (default 50 ms
or 32 items), coalescing duplicate updates by optional ``key``, and executes the
entire batch inside ONE SQLite ``BEGIN IMMEDIATE`` transaction using per-item
``SAVEPOINT`` isolation so one failing callback never aborts healthy siblings.

The worker thread is started lazily on first enqueue and drained synchronously
when ``close()`` or ``flush()`` is called.
"""

from __future__ import annotations

from collections import deque
import logging
from threading import Condition, Thread, current_thread
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class WriteBehindBatcher:
    """Thread-safe write-behind micro-batcher attached to one ``StorageRuntime``."""

    DEFAULT_WINDOW_SECONDS = 0.05
    DEFAULT_MAX_BATCH_SIZE = 32

    def __init__(
        self,
        runtime: Any,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    ) -> None:
        self._runtime = runtime
        self._window_seconds = max(0.0, float(window_seconds))
        self._max_batch_size = max(1, int(max_batch_size))
        self._condition = Condition()
        self._queue: deque[list[Any]] = deque()
        self._by_key: dict[str, list[Any]] = {}
        self._enqueue_seq = 0
        self._committed_seq = 0
        self._flush_requested = False
        self._closing = False
        self._closed = False
        self._thread: Thread | None = None
        self._flushed_items = 0
        self._flushed_batches = 0
        self._coalesced = 0
        self._errors = 0

    def enqueue(self, callback: Callable[[Any], Any], *, key: str | None = None) -> bool:
        """Schedule *callback(connection)* for the next micro-batch commit.

        When *key* is provided and a pending entry with the same *key* has not
        yet been picked up for commit, its callback is replaced in place.
        Returns False if the batcher is already closing/closed.
        """
        if not callable(callback):
            raise TypeError("batched write callback must be callable")
        with self._condition:
            if self._closing or self._closed:
                return False
            self._enqueue_seq += 1
            seq = self._enqueue_seq
            if key is not None and key in self._by_key:
                entry = self._by_key[key]
                entry[1] = callback
                entry[2] = seq
                self._coalesced += 1
            else:
                entry = [key, callback, seq]
                self._queue.append(entry)
                if key is not None:
                    self._by_key[key] = entry
            if self._thread is None:
                worker = Thread(
                    target=self._run,
                    name="v2-sqlite-write-batcher",
                    daemon=True,
                )
                self._thread = worker
                worker.start()
            self._condition.notify_all()
            return True

    def flush(self, *, timeout: float = 5.0) -> int:
        """Wait until all previously enqueued callbacks have been committed."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            if self._thread is None:
                return self._flushed_items
            if current_thread() is self._thread:
                return self._flushed_items
            target_seq = self._enqueue_seq
            if self._committed_seq >= target_seq:
                return self._flushed_items
            self._flush_requested = True
            self._condition.notify_all()
            while self._committed_seq < target_seq and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._flushed_items

    def close(self, *, timeout: float = 5.0) -> None:
        """Drain all queued writes and stop the background flusher thread."""
        with self._condition:
            self._closing = True
            worker = self._thread
            self._condition.notify_all()
        if worker is not None and worker is not current_thread():
            worker.join(timeout=max(0.1, float(timeout)))
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def configure(
        self,
        *,
        window_ms: int | None = None,
        max_batch_size: int | None = None,
    ) -> None:
        """Hot-update coalescing window or batch ceiling without stopping the worker."""
        with self._condition:
            if window_ms is not None:
                self._window_seconds = max(0.0, min(0.5, float(window_ms) / 1000.0))
            if max_batch_size is not None:
                self._max_batch_size = max(1, int(max_batch_size))
            self._condition.notify_all()

    def stats(self) -> dict[str, int]:
        with self._condition:
            return {
                "queued": len(self._queue),
                "flushed_items": self._flushed_items,
                "flushed_batches": self._flushed_batches,
                "coalesced": self._coalesced,
                "errors": self._errors,
                "window_ms": round(self._window_seconds * 1000),
            }

    def _pop_batch_locked(self) -> tuple[list[list[Any]], int]:
        batch: list[list[Any]] = []
        max_seq = self._committed_seq
        while self._queue and len(batch) < self._max_batch_size:
            entry = self._queue.popleft()
            key = entry[0]
            if key is not None and self._by_key.get(key) is entry:
                del self._by_key[key]
            if entry[2] > max_seq:
                max_seq = entry[2]
            batch.append(entry)
        if not self._queue:
            self._flush_requested = False
        return batch, max_seq

    def _commit_batch(self, batch: list[list[Any]]) -> tuple[int, int]:
        succeeded = 0
        errors = 0
        try:
            with self._runtime.transaction() as connection:
                for idx, (_key, callback, _seq) in enumerate(batch):
                    savepoint = f"v2_wb_{idx}"
                    connection.execute(f"SAVEPOINT {savepoint}")
                    try:
                        callback(connection)
                        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                        succeeded += 1
                    except Exception as error:
                        try:
                            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                        except Exception:
                            pass
                        errors += 1
                        logger.warning(
                            "V2 SQLite write-behind callback failed error_type=%s",
                            type(error).__name__,
                        )
        except Exception as error:
            errors += len(batch) - succeeded
            logger.warning(
                "V2 SQLite write-behind batch transaction failed size=%d error_type=%s",
                len(batch),
                type(error).__name__,
            )
        return succeeded, errors

    def _run(self) -> None:
        try:
            while True:
                batch: list[list[Any]] = []
                max_seq = 0
                with self._condition:
                    while not self._queue and not self._closing:
                        self._condition.wait()
                    if not self._queue and self._closing:
                        return
                    # Wait up to window_seconds for sibling writes to coalesce unless
                    # batch is full, an explicit flush was requested, or we are closing.
                    if (
                        self._window_seconds > 0.0
                        and len(self._queue) < self._max_batch_size
                        and not self._flush_requested
                        and not self._closing
                    ):
                        deadline = time.monotonic() + self._window_seconds
                        while (
                            self._window_seconds > 0.0
                            and len(self._queue) < self._max_batch_size
                            and not self._flush_requested
                            and not self._closing
                        ):
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            self._condition.wait(timeout=remaining)
                    batch, max_seq = self._pop_batch_locked()

                if batch:
                    succeeded, errors = self._commit_batch(batch)
                    with self._condition:
                        self._flushed_batches += 1
                        self._flushed_items += succeeded
                        self._errors += errors
                        if self._queue:
                            min_pending = min(item[2] for item in self._queue)
                            safe_seq = min(max_seq, min_pending - 1)
                        else:
                            safe_seq = max_seq
                        if safe_seq > self._committed_seq:
                            self._committed_seq = safe_seq
                        self._condition.notify_all()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()
