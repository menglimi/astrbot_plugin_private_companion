"""Dashboard Web API and host runtime sync for unified 5-tier performance tuning."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from typing import Any

from astrbot.api.web import request
from companion_v2.execution.models import TaskError
from companion_v2.foundation.outcome import Outcome
from companion_v2.storage.tuning_config import (
    DEFAULT_PROFILE,
    PROFILE_IDS,
    PerformancePlan,
    active_performance_plan,
    clear_active_performance_plan,
    compact_sparse_overrides,
    normalize_tuning_config,
    resolve_performance_plan,
    set_active_performance_plan,
)

from .backup_api import MAX_REQUEST_BYTES
from .management_identity import current_request_username, require_controller_management_operator
from .performance_mode import performance_enabled

logger = logging.getLogger(__name__)
PREFIX = "/astrbot_plugin_private_companion/performance/tuning"
_FALLBACK_TUNING_LOCK: asyncio.Lock | None = None


def tuning_request_lock(controller: Any) -> asyncio.Lock:
    """Serialize tuning reads/writes per controller without blocking unrelated hosts."""
    global _FALLBACK_TUNING_LOCK
    lock = getattr(controller, "_tuning_request_lock", None)
    if isinstance(lock, asyncio.Lock):
        return lock
    created = asyncio.Lock()
    try:
        setattr(controller, "_tuning_request_lock", created)
    except AttributeError:
        if _FALLBACK_TUNING_LOCK is None:
            _FALLBACK_TUNING_LOCK = asyncio.Lock()
        return _FALLBACK_TUNING_LOCK
    return created


def apply_host_performance_tuning(
    controller: Any,
    *,
    runtime: Any = None,
    config: Any = None,
    total_memory_bytes: int | None = None,
    cpu_count: int | None = None,
    is_32bit: bool | None = None,
) -> tuple[PerformancePlan, dict[str, Any]]:
    """Resolve and hot-apply performance settings to runtime, batcher, sidecars, and task slots."""
    effective_config = config if config is not None else getattr(controller, "config", None)
    plan = resolve_performance_plan(
        effective_config,
        total_memory_bytes=total_memory_bytes,
        cpu_count=cpu_count,
        is_32bit=is_32bit,
    )
    set_active_performance_plan(plan)
    try:
        setattr(controller, "_active_performance_plan", plan)
    except AttributeError:
        pass

    applied: dict[str, Any] = {
        "sqlite_applied": False,
        "background_slots_applied": False,
        "tts_max_concurrency": plan.values["tts_max_concurrency"],
        "telemetry_ttl_days": plan.values["telemetry_ttl_days"],
        "write_batch_window_ms": plan.values["write_batch_window_ms"],
    }

    target_runtime = runtime if runtime is not None else getattr(controller, "runtime", None)
    session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
    application = getattr(session, "application", None) if session is not None else None
    if target_runtime is None and application is not None:
        target_runtime = getattr(application, "runtime", None)

    if target_runtime is not None and callable(getattr(target_runtime, "apply_tuning_profile", None)):
        applied["sqlite"] = target_runtime.apply_tuning_profile(
            plan.sqlite_profile,
            write_batch_window_ms=int(plan.values["write_batch_window_ms"]),
        )
        applied["sqlite_applied"] = True

    if application is not None:
        bg_slots = int(plan.values["background_slots"])
        tasks = getattr(application, "tasks", None)
        updater = getattr(tasks, "update_background_slots", None)
        if callable(updater):
            try:
                updater(bg_slots)
                applied["background_slots_applied"] = True
            except Exception as error:
                logger.warning(
                    "[CompanionV2] 后台并发槽位热更新跳过 error_type=%s",
                    type(error).__name__,
                )
        outbound = getattr(application, "outbound", None)
        if outbound is not None and hasattr(outbound, "concurrency"):
            try:
                outbound.concurrency = bg_slots
            except Exception:
                pass

    return plan, applied


def release_host_performance_tuning(controller: Any) -> None:
    """Clear the process-wide performance plan if this controller published it."""
    published = getattr(controller, "_active_performance_plan", None)
    if published is None:
        return
    try:
        setattr(controller, "_active_performance_plan", None)
    except AttributeError:
        pass
    if active_performance_plan() is published:
        clear_active_performance_plan()


class TuningWebAPI:
    """Expose GET/POST endpoints for 5-tier performance tuning and sparse overrides."""

    def __init__(self, controller: Any) -> None:
        self.controller = controller

    def routes(self):
        return [
            (PREFIX, self.status, ["GET"], "Read V2 hardware profile and performance tuning plan"),
            (PREFIX, self.update, ["POST"], "Save and hot-apply V2 performance tuning settings"),
        ]

    def _operator(self):
        username = current_request_username(request)
        return require_controller_management_operator(self.controller, username)

    @staticmethod
    def _body(raw: bytes) -> Any:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result

        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )

    def _runtime_snapshot(self, plan: PerformancePlan) -> dict[str, Any]:
        controller = self.controller
        runtime = getattr(controller, "runtime", None)
        session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
        application = getattr(session, "application", None) if session is not None else None
        if runtime is None and application is not None:
            runtime = getattr(application, "runtime", None)
        profile = getattr(runtime, "tuning_profile", None) if runtime is not None else plan.sqlite_profile
        batch_stats = runtime.batched_write_stats() if runtime is not None and callable(getattr(runtime, "batched_write_stats", None)) else None
        tasks = getattr(application, "tasks", None) if application is not None else None
        limits = getattr(tasks, "limits", None) if tasks is not None else None
        return {
            "runtime_ready": runtime is not None,
            "tuning_tier": getattr(profile, "tier", plan.sqlite_profile.tier),
            "reader_pool_size": getattr(runtime, "reader_pool_size", plan.sqlite_profile.reader_pool_size),
            "cache_size_kib": getattr(profile, "cache_size_kib", plan.sqlite_profile.cache_size_kib),
            "mmap_size": getattr(profile, "mmap_size", plan.sqlite_profile.mmap_size),
            "synchronous": (
                profile.effective_synchronous()
                if hasattr(profile, "effective_synchronous")
                else plan.sqlite_profile.effective_synchronous()
            ),
            "background_slots": getattr(limits, "background_slots", plan.values["background_slots"]),
            "tts_max_concurrency": plan.values["tts_max_concurrency"],
            "telemetry_ttl_days": plan.values["telemetry_ttl_days"],
            "write_batch_window_ms": plan.values["write_batch_window_ms"],
            "batched_writes": batch_stats,
        }

    def _payload(self, plan: PerformancePlan, *, applied: dict[str, Any] | None = None) -> dict[str, Any]:
        controller = self.controller
        config = controller.config if isinstance(getattr(controller, "config", None), dict) else {}
        saved_lite = performance_enabled(config)
        effective_lite = getattr(controller, "_performance_mode_enabled", saved_lite)
        data = plan.to_payload(
            lite_page_enabled=saved_lite,
            lite_page_effective=bool(effective_lite),
        )
        data["runtime"] = self._runtime_snapshot(plan)
        if applied is not None:
            data["applied"] = applied
        return data

    def _parse_update(self, document: dict[str, Any], current_config: dict[str, Any]) -> tuple[str, dict[str, Any], bool | None]:
        allowed = {"action", "profile", "overrides", "lite_page_enabled"}
        if not set(document) <= allowed:
            raise ValueError("unexpected fields")

        action = document.get("action", "save")
        if action not in ("save", "reset", "lite_page"):
            raise ValueError("invalid action")

        current_profile, current_overrides = normalize_tuning_config(current_config)

        lite_page_enabled: bool | None = None
        if "lite_page_enabled" in document:
            if type(document["lite_page_enabled"]) is not bool:
                raise ValueError("invalid lite_page_enabled")
            lite_page_enabled = document["lite_page_enabled"]

        if action == "lite_page":
            if lite_page_enabled is None:
                raise ValueError("lite_page_enabled required")
            return current_profile, current_overrides, lite_page_enabled

        if "profile" in document:
            raw_profile = document["profile"]
            if type(raw_profile) is not str or raw_profile.strip().lower() not in PROFILE_IDS:
                raise ValueError("invalid profile")
            next_profile = raw_profile.strip().lower()
        else:
            next_profile = current_profile or DEFAULT_PROFILE

        if action == "reset":
            return next_profile, {}, lite_page_enabled

        if "overrides" in document:
            raw_overrides = document["overrides"]
            if raw_overrides is None:
                next_overrides: dict[str, Any] = {}
            else:
                if type(raw_overrides) is not dict:
                    raise ValueError("invalid overrides")
                _, validated = normalize_tuning_config(
                    {"profile": next_profile, "overrides": raw_overrides},
                    strict=True,
                )
                next_overrides = validated
        else:
            next_overrides = dict(current_overrides)

        return next_profile, next_overrides, lite_page_enabled

    async def status(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            if request.query:
                raise ValueError("unexpected query parameters")
            async with tuning_request_lock(controller):
                async with controller._control_lock:
                    self._operator()
                    if getattr(controller, "_terminated", False):
                        raise TaskError("staging_host_terminated", "plugin is stopped")
                    config_copy = deepcopy(dict(controller.config)) if isinstance(controller.config, dict) else {}
                plan = await asyncio.to_thread(resolve_performance_plan, config_copy)
                return {"status": "ok", "data": self._payload(plan)}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except ValueError:
            return outcome_response(Outcome.failed("tuning_request_invalid", "performance tuning request is invalid"))

    async def update(self):
        from .web_api import outcome_response
        controller = self.controller
        try:
            raw = await request.body()
            if len(raw) > MAX_REQUEST_BYTES or request.query:
                raise ValueError("request too large or query provided")
            document = self._body(raw) if raw and raw.strip() else {}
            if type(document) is not dict:
                raise ValueError("object required")

            async with tuning_request_lock(controller):
                async with controller._control_lock:
                    self._operator()
                    if getattr(controller, "_terminated", False):
                        raise TaskError("staging_host_terminated", "plugin is stopped")
                    save = getattr(controller, "save_plugin_config", None)
                    if not callable(save):
                        raise TaskError(
                            "tuning_config_save_unavailable",
                            "plugin configuration cannot be saved",
                        )
                    current_config = deepcopy(dict(controller.config)) if isinstance(controller.config, dict) else {}
                    next_profile, parsed_overrides, lite_toggle = self._parse_update(document, current_config)

                    preview_plan = resolve_performance_plan(
                        {"profile": next_profile, "overrides": parsed_overrides},
                        strict=True,
                    )
                    sparse_overrides = compact_sparse_overrides(
                        next_profile,
                        parsed_overrides,
                        hardware=preview_plan.hardware,
                    )

                    candidate = deepcopy(current_config)
                    perf_section = candidate.get("performance")
                    if not isinstance(perf_section, dict):
                        perf_section = {}
                    else:
                        perf_section = dict(perf_section)
                    perf_section["profile"] = next_profile
                    perf_section["overrides"] = sparse_overrides
                    if lite_toggle is not None:
                        perf_section["enabled"] = lite_toggle
                    elif "enabled" not in perf_section:
                        perf_section["enabled"] = False
                    candidate["performance"] = perf_section

                    try:
                        save(candidate)
                    except Exception as error:
                        raise TaskError(
                            "tuning_config_save_failed",
                            "performance tuning settings could not be saved",
                        ) from error

                plan, applied = await asyncio.to_thread(
                    apply_host_performance_tuning,
                    controller,
                    config=candidate,
                )
                return {"status": "ok", "data": self._payload(plan, applied=applied)}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
            return outcome_response(
                Outcome.failed("tuning_request_invalid", "performance tuning settings are invalid")
            )
