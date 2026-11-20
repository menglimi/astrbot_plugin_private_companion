"""Bounded async concurrency slots and singleflight deduplication for video work."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import threading
import weakref


class QueueTimeout(TimeoutError):
    """Raised when the bounded video processing queue is full or timed out."""

    code = "host_video_queue_timeout"


def format_seconds(seconds: float) -> str:
    """Format seconds cleanly for status messages without exposing internals."""
    value = max(0.0, float(seconds))
    if value < 60:
        return f"{value:.1f} 秒"
    minutes, rem = divmod(int(round(value)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {rem} 秒"
    return f"{minutes} 分 {rem} 秒"


class SlotLimiter:
    """Bounded async semaphore + queue backed by a dedicated thread pool."""

    def __init__(self, concurrency: int = 2, max_queue: int = 8, wait_seconds: float = 30.0):
        self.concurrency = max(1, int(concurrency))
        self.max_queue = max(0, int(max_queue))
        self.default_wait_seconds = max(0.1, float(wait_seconds))
        self._pool = ThreadPoolExecutor(
            max_workers=self.concurrency,
            thread_name_prefix="companion-video",
        )
        self._state_lock = threading.Lock()
        self._loop_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )
        self._waiting = 0
        self._closed = False

    def _semaphore_for(self, loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
        with self._state_lock:
            sem = self._loop_semaphores.get(loop)
            if sem is None:
                sem = asyncio.Semaphore(self.concurrency)
                self._loop_semaphores[loop] = sem
            return sem

    async def run(self, fn, *args, wait_seconds: float | None = None, **kwargs):
        """Acquire a queue slot cleanly, execute ``fn`` on the dedicated pool, and release."""
        loop = asyncio.get_running_loop()
        timeout = self.default_wait_seconds if wait_seconds is None else max(0.01, float(wait_seconds))
        with self._state_lock:
            if self._closed:
                raise QueueTimeout("视频处理通道已关闭。")
            if self._waiting >= self.max_queue + self.concurrency:
                raise QueueTimeout(
                    f"视频压缩队列繁忙（同时处理上限 {self.concurrency}，排队上限 {self.max_queue}），请稍后重试。"
                )
            self._waiting += 1
        sem = self._semaphore_for(loop)
        acquired = False
        try:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=timeout)
                acquired = True
            except asyncio.TimeoutError as error:
                raise QueueTimeout(
                    f"等待视频处理槽位超时（超过 {format_seconds(timeout)}）。"
                ) from error
            call = functools.partial(fn, *args, **kwargs)
            return await loop.run_in_executor(self._pool, call)
        finally:
            if acquired:
                sem.release()
            with self._state_lock:
                self._waiting = max(0, self._waiting - 1)

    def shutdown(self, wait: bool = False) -> None:
        """Close the dedicated thread pool on plugin termination."""
        with self._state_lock:
            self._closed = True
        self._pool.shutdown(wait=wait)


def _consume_exception(done_task: asyncio.Future) -> None:
    """Mark abandoned task exceptions as retrieved so asyncio never logs warnings."""
    if done_task.cancelled():
        return
    try:
        done_task.exception()
    except BaseException:
        pass


class SingleFlight:
    """Coalesce concurrent async calls for identical keys per event loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._inflight: dict[tuple[int, str], tuple[asyncio.Task, int]] = {}

    async def do(self, key: str, coro_fn, *, timeout: float | None = None):
        """Run ``coro_fn()`` once for concurrent waiters sharing ``key`` on the same loop.

        When ``timeout`` is supplied, each waiter enforces its own remaining deadline
        outside ``asyncio.shield(task)`` via ``asyncio.wait_for``. A short-deadline
        waiter times out cleanly without cancelling the shared task for longer-deadline
        waiters; the underlying task is cancelled only once the active waiter refcount
        reaches zero.
        """
        timeout_value: float | None = None
        if timeout is not None:
            timeout_value = float(timeout)
            if timeout_value <= 0:
                raise asyncio.TimeoutError()

        loop = asyncio.get_running_loop()
        flight_key = (id(loop), str(key))
        with self._lock:
            entry = self._inflight.get(flight_key)
            if entry is None or entry[0].done():
                task = asyncio.ensure_future(coro_fn())
                task.add_done_callback(_consume_exception)
                self._inflight[flight_key] = (task, 1)
            else:
                task, waiters = entry
                self._inflight[flight_key] = (task, waiters + 1)

        try:
            shielded = asyncio.shield(task)
            if timeout_value is None:
                return await shielded
            return await asyncio.wait_for(shielded, timeout=timeout_value)
        finally:
            cancel_task = None
            with self._lock:
                current = self._inflight.get(flight_key)
                if current is not None and current[0] is task:
                    remaining = current[1] - 1
                    if remaining <= 0:
                        self._inflight.pop(flight_key, None)
                        if not task.done():
                            cancel_task = task
                    else:
                        self._inflight[flight_key] = (task, remaining)
            if cancel_task is not None:
                cancel_task.cancel()
