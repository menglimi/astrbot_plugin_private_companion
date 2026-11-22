"""Read the authorized phone foreground and location summaries for one private core reply."""

from __future__ import annotations

import logging

from companion_v2.conversation.location_context import project_location_context
from companion_v2.conversation.phone_context import project_phone_context

from .core_reply_health import authorized_mobile_snapshot


logger = logging.getLogger(__name__)


def phone_scene_snapshot(application, context, at):
    """Return the phone's recent foreground summary, or None when unusable.

    The snapshot is read through the same private-window consent gate as health
    material, so an unauthorized or disabled Reality capability never reaches
    the prompt.
    """
    snapshot = authorized_mobile_snapshot(application, context)
    if snapshot is None:
        return None
    try:
        return project_phone_context(snapshot, observed_at=at)
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复手机场景降级 error_type=%s", type(error).__name__)
        return None


def location_scene_snapshot(application, context, at):
    """Return a bounded, desensitized mobile location summary for private chat only.

    Shares the exact private-window, role-enablement and capability-consent gate
    used by health and foreground phone activity; group chats always get None.
    """
    snapshot = authorized_mobile_snapshot(application, context)
    if snapshot is None:
        return None
    try:
        return project_location_context(snapshot, observed_at=at)
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复手机位置降级 error_type=%s", type(error).__name__)
        return None


__all__ = ["location_scene_snapshot", "phone_scene_snapshot"]
