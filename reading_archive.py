# -*- coding: utf-8 -*-
"""Compatibility hooks for the retired external reading source.

The public companion package no longer ships an external content connector.
These no-op hooks keep older state/configuration harmless during upgrade and
let callers degrade to the normal companion flow without network access.
"""
from __future__ import annotations

from typing import Any


class ReadingArchiveMixin:
    """Provide inert compatibility methods for removed reading actions."""

    def _reading_archive_available(self) -> bool:
        return False

    def _reading_archive_read_available(self, user: dict[str, Any] | None = None) -> bool:
        return False

    async def _maybe_trigger_reading_archive_boredom_read(self) -> None:
        return None

    async def _maybe_schedule_reading_archive_recommendation_request(self) -> None:
        return None

    def _format_reading_archive_preference_influence_for_reply(
        self,
        inbound_text: str,
        user: dict[str, Any] | None,
    ) -> str:
        return ""

    def _format_reading_archive_action_context(self, user: dict[str, Any]) -> str:
        return ""

    def _self_timeline_from_reading_archive(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    async def _run_reading_archive_read_action(self, user: dict[str, Any] | None = None) -> None:
        return None
