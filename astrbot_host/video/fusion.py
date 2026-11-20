"""Orchestrated compression, bounded caching and primary/fallback routing for Core video understanding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time

from companion_v2.execution.models import TaskError

from .cache import (
    CachedCompressedVideo,
    CompressCache,
    VisionCache,
    VisionFlights,
    build_compress_cache_key,
)
from .compressor import (
    DEFAULT_MAX_DURATION_SECONDS,
    DEFAULT_TARGET_BYTES,
    MAX_DATA_URI_BYTES,
    CompressionResult,
    compress_video,
)
from .limits import QueueTimeout, SingleFlight, SlotLimiter, format_seconds
from .openai_compat_video import sanitize_video_error


MAX_CORE_VIDEO_DOWNLOAD_BYTES = 80 * 1024 * 1024


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class VideoAnalysisOutcome:
    status: str  # "summary" | "rejected" | "failed"
    text: str = ""
    error_reason: str = ""
    prepared_bytes: bytes = b""
    mime_type: str = "video/mp4"
    compressed: bool = False
    truncated: bool = False


class VideoFusionService:
    """Shared bounded video preparation and vision relay runtime."""

    def __init__(
        self,
        *,
        concurrency: int = 2,
        max_queue: int = 8,
        compress_wait_seconds: float = 35.0,
        compress_cache: CompressCache | None = None,
        vision_cache: VisionCache | None = None,
    ):
        self.limiter = SlotLimiter(
            concurrency=concurrency,
            max_queue=max_queue,
            wait_seconds=compress_wait_seconds,
        )
        self.compress_flights = SingleFlight()
        self.compress_cache = compress_cache or CompressCache()
        self.vision_cache = vision_cache or VisionCache()
        self.vision_flights = VisionFlights()

    def close(self) -> None:
        """Shut down the dedicated video thread pool."""
        self.limiter.shutdown(wait=False)

    async def prepare_video(
        self,
        raw_bytes: bytes,
        *,
        suffix: str = ".mp4",
        settings: dict,
        compress_wait_seconds: float | None = None,
    ) -> CompressionResult:
        """Deduplicate, cache, and boundedly compress/truncate raw video bytes."""
        target_mb = settings.get("video_understanding_target_mb", 10)
        try:
            target_bytes = min(MAX_DATA_URI_BYTES, max(1, int(target_mb)) * 1024 * 1024)
        except (TypeError, ValueError):
            target_bytes = DEFAULT_TARGET_BYTES

        max_duration_raw = settings.get("video_understanding_max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS)
        try:
            max_duration_s = max(1.0, float(max_duration_raw))
        except (TypeError, ValueError):
            max_duration_s = DEFAULT_MAX_DURATION_SECONDS

        action = str(settings.get("video_understanding_oversize_action") or "compress")
        ext = (Path(f"clip{suffix}").suffix or ".mp4").lower()
        source_sha256 = await asyncio.to_thread(_sha256_hex, raw_bytes)
        cache_key = build_compress_cache_key(
            source_sha256=source_sha256,
            target_bytes=target_bytes,
            max_duration_s=max_duration_s,
            oversize_action=action,
        )

        # Reject action for oversize bytes avoids unnecessary cache/slot usage.
        if action != "reject":
            cached = await asyncio.to_thread(self.compress_cache.get, cache_key)
            if cached is not None:
                return CompressionResult(
                    content=cached.content,
                    mime_type=cached.mime_type,
                    compressed=cached.compressed,
                    truncated=cached.truncated,
                    duration_s=cached.duration_s,
                    original_duration_s=cached.original_duration_s,
                    original_bytes=cached.original_bytes,
                )

        async def run_prepare():
            return await self.limiter.run(
                compress_video,
                raw_bytes,
                suffix=ext,
                target_bytes=target_bytes,
                max_duration_s=max_duration_s,
                oversize_action=action,
                wait_seconds=compress_wait_seconds,
            )

        result = await self.compress_flights.do(cache_key, run_prepare)
        if action != "reject" and result.compressed:
            await asyncio.to_thread(
                self.compress_cache.put,
                cache_key,
                CachedCompressedVideo(
                    content=result.content,
                    mime_type=result.mime_type,
                    compressed=result.compressed,
                    truncated=result.truncated,
                    duration_s=result.duration_s,
                    original_duration_s=result.original_duration_s,
                    original_bytes=result.original_bytes,
                ),
            )
        return result

    async def analyze_video(
        self,
        raw_bytes: bytes,
        *,
        suffix: str = ".mp4",
        routes: tuple,
        settings: dict,
        question: str = "",
        summarize_fn,
        deadline_seconds: float | None = None,
    ) -> VideoAnalysisOutcome:
        """Prepare video bytes and run prioritized video understanding routes with transparent failure text."""
        try:
            prepared = await self.prepare_video(raw_bytes, suffix=suffix, settings=settings)
        except QueueTimeout as error:
            return VideoAnalysisOutcome(
                status="failed",
                error_reason=str(error),
                prepared_bytes=raw_bytes,
            )
        except TaskError as error:
            if error.code in ("host_video_limit", "host_video_format_unsupported"):
                return VideoAnalysisOutcome(
                    status="rejected",
                    error_reason=str(error),
                )
            return VideoAnalysisOutcome(
                status="failed",
                error_reason=str(error),
                prepared_bytes=raw_bytes,
            )

        if not routes:
            return VideoAnalysisOutcome(
                status="failed",
                error_reason="未找到可用的视频理解模型或视觉中转路由。",
                prepared_bytes=prepared.content,
                mime_type=prepared.mime_type,
                compressed=prepared.compressed,
                truncated=prepared.truncated,
            )

        budget = (
            float(deadline_seconds)
            if deadline_seconds is not None
            else float(settings.get("video_understanding_wait_seconds", 45))
        )
        deadline = time.monotonic() + max(0.0, budget)
        errors: list[str] = []
        for provider, model in routes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("等待总预算已耗尽")
                break
            try:
                text = await asyncio.wait_for(
                    summarize_fn(
                        prepared.content,
                        provider,
                        model,
                        {**settings, "video_understanding_wait_seconds": remaining},
                        mime_type=prepared.mime_type,
                        question=question,
                        vision_cache=self.vision_cache,
                        vision_flights=self.vision_flights,
                    ),
                    timeout=remaining,
                )
                return VideoAnalysisOutcome(
                    status="summary",
                    text=text,
                    prepared_bytes=prepared.content,
                    mime_type=prepared.mime_type,
                    compressed=prepared.compressed,
                    truncated=prepared.truncated,
                )
            except asyncio.TimeoutError:
                provider_label = str(getattr(provider, "provider_config", {}).get("id") or model or "route")
                errors.append(f"{provider_label}: 视频理解超时（{format_seconds(remaining)}）")
            except TaskError as error:
                provider_label = str(getattr(provider, "provider_config", {}).get("id") or model or "route")
                errors.append(f"{provider_label}: {sanitize_video_error(error)}")
            except Exception as error:
                provider_label = str(getattr(provider, "provider_config", {}).get("id") or model or "route")
                errors.append(f"{provider_label}: {sanitize_video_error(error)}")

        return VideoAnalysisOutcome(
            status="failed",
            error_reason="；".join(errors)[:320] or "视频理解路由全部调用失败。",
            prepared_bytes=prepared.content,
            mime_type=prepared.mime_type,
            compressed=prepared.compressed,
            truncated=prepared.truncated,
        )
