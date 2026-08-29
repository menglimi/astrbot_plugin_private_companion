# -*- coding: utf-8 -*-
"""Migration-only compatibility shim for data created by older releases.

The companion plugin no longer implements or exposes an archive reader.  The
methods remain inert so old proactive state can be loaded without exceptions;
the story/creative extension owns any migrated content.
"""
from __future__ import annotations

from typing import Any


class ReadingArchiveMixin:
    def _reading_archive_available(self) -> bool:
        return False

    def _reading_archive_read_available(self, user: dict[str, Any] | None = None) -> bool:
        return False

    async def _maybe_trigger_reading_archive_boredom_read(self) -> None:
        return None

    async def _maybe_schedule_reading_archive_recommendation_request(self) -> None:
        return None

    def _format_reading_archive_preference_influence_for_reply(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def _format_reading_archive_action_context(self, *args: Any, **kwargs: Any) -> str:
        return ""

    def _format_bookshelf_reading_context_for_reply(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Keep the removed reader's context hook inert for old state."""
        return ""

    def _self_timeline_from_reading_archive(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def _run_reading_archive_read_action(self, *args: Any, **kwargs: Any) -> None:
        return None
