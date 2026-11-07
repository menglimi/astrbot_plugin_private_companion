"""Read the authorized phone foreground summary for one private core reply."""

from __future__ import annotations

import logging

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


__all__ = ["phone_scene_snapshot"]
