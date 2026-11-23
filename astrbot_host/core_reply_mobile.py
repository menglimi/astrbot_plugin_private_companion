"""Read authorized phone, location and screen summaries for one private core reply."""

from __future__ import annotations

import logging

from companion_v2.conversation.location_context import project_location_context
from companion_v2.conversation.phone_context import project_phone_context
from companion_v2.conversation.screen_context import project_screen_context
from companion_v2.execution.models import require
from companion_v2.foundation.refs import ScopeKind

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


def screen_scene_snapshot(application, context, at):
    """Return the newest desensitized desktop screen summary within 15 min for private owner chat only.

    Strictly isolated to the exact authorized private owner window: group chats,
    other users, unconsented scopes, and analyses older than 15 minutes always get None.
    """
    scope = getattr(context, "scope", None)
    if getattr(scope, "kind", None) is not ScopeKind.PRIVATE:
        return None
    principal = getattr(getattr(context, "actor", None), "principal", None)
    account_text = str(getattr(principal, "native_account_id", "") or "").strip() if principal is not None else ""
    scope_text = str(getattr(scope, "native_scope_id", "") or "").strip()
    if not account_text or not scope_text or scope_text == "*" or account_text != scope_text:
        return None
    capabilities = getattr(application, "capabilities", None)
    role = getattr(getattr(context, "role_binding", None), "role", None)
    if (capabilities is None or role is None
            or not callable(getattr(capabilities, "require_role_enabled", None))):
        return None
    try:
        from companion_v2.screen.repositories import SCREEN_EXTENSION
        if not capabilities.require_role_enabled(
            extension_id=SCREEN_EXTENSION,
            role=role,
            bot_id=getattr(scope, "bot_id", None),
        ).is_success:
            return None
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复屏幕角色授权校验降级 error_type=%s", type(error).__name__)
        return None
    repository = getattr(application, "screen_repository", None)
    if repository is None or not callable(getattr(repository, "latest_analysis", None)):
        return None
    try:
        materials = require(repository.latest_analysis(
            scope=scope, resolved=context.actor, observed_at=at,
        ))
        if not materials:
            return None
        item = materials[0]
        analysis = getattr(item, "analysis", None)
        capture = getattr(getattr(item, "material", None), "capture", None)
        if analysis is None or capture is None or getattr(capture, "captured_at", None) is None:
            return None
        return project_screen_context(
            analysis.interpretation,
            captured_at=capture.captured_at,
            observed_at=at,
        )
    except Exception as error:
        logger.warning("[CompanionV2] 核心回复桌面屏幕感知降级 error_type=%s", type(error).__name__)
        return None


__all__ = ["location_scene_snapshot", "phone_scene_snapshot", "screen_scene_snapshot"]
