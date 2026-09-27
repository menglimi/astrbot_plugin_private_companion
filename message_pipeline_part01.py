# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import wraps
from typing import Any


def _coalesce_event_data_saves(handler: Any) -> Any:
    """Submit all durable mutations from one message handler as one request."""

    @wraps(handler)
    async def wrapped(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
        starter = getattr(self, "_begin_event_data_save_batch", None)
        finisher = getattr(self, "_finish_event_data_save_batch", None)
        handle = starter(event) if callable(starter) else None
        try:
            return await handler(self, event, *args, **kwargs)
        finally:
            if callable(finisher):
                finisher(handle)

    return wrapped

def event_data_save_boundary(handler: Any = None, *, flush: bool = False) -> Any:
    """Share an event-owned save batch across early and final message hooks."""

    if handler is None:
        return lambda actual: event_data_save_boundary(actual, flush=flush)

    @wraps(handler)
    async def wrapped(self: Any, event: Any, *args: Any, **kwargs: Any) -> Any:
        starter = getattr(self, "_begin_event_data_save_batch", None)
        finisher = getattr(self, "_finish_event_data_save_batch", None)
        suspender = getattr(self, "_suspend_event_data_save_batch", None)
        handle = starter(event) if callable(starter) else None
        completed = False
        try:
            result = await handler(self, event, *args, **kwargs)
            completed = True
            return result
        finally:
            if handle and callable(finisher):
                stopped = False
                is_stopped = getattr(event, "is_stopped", None)
                if callable(is_stopped):
                    try:
                        stopped = bool(is_stopped())
                    except Exception:
                        stopped = False
                if flush or stopped or not completed:
                    finisher(handle)
                elif callable(suspender):
                    suspender(handle)

    return wrapped

def _persona_value(owner: Any, key: str, default: Any = None) -> Any:
    """Read the active persona setting, with a legacy harness fallback."""
    getter = getattr(owner, "persona_setting", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            pass
    return getattr(owner, key, default)

def _persona_feature_enabled(owner: Any, key: str, default: bool = False) -> bool:
    """Apply a persona-scoped feature flag and retain proactive-only unlocks."""
    if not hasattr(owner, "enable_multi_persona_mode"):
        checker = getattr(owner, "_feature_enabled_or_temp_unlocked", None)
        if callable(checker):
            try:
                return bool(checker(key, default))
            except Exception:
                pass
    if bool(_persona_value(owner, key, default)):
        return True
    unlocker = getattr(owner, "_proactive_only_temp_unlock_allows", None)
    return bool(
        _persona_value(owner, "enable_proactive_only_mode", False)
        and callable(unlocker)
        and unlocker(key)
    )
