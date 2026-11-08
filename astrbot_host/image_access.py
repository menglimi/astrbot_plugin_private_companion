"""补齐已安装原生生图能力在真实私聊上下文中的默认启用记录。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging

from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityDesiredState
from companion_v2.capabilities.repositories import CapabilityRepository
from companion_v2.execution.models import TaskContext, digest
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.models import AuthorizationTier, GrantState
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.runtime import StorageRuntime


logger = logging.getLogger(__name__)

IMAGE_EXTENSION = "image"
IMAGE_CAPABILITIES = ("image.generate", "image.edit", "image.mask_edit")
GROUP_IMAGE_CAPABILITIES = ("image.generate", "image.edit")


def ensure_image_access(runtime: StorageRuntime, context: TaskContext, *, observed_at: datetime) -> None:
    """补齐原生生图能力在当前已验证会话中的独立授权记录。

    私聊沿用原有默认值；群聊只在角色生图配置明确允许当前发言人时，
    为该群聊窗口签发 ``image.generate`` / ``image.edit``。群聊授权与私聊
    完全隔离，且本函数不签发屏幕等其他能力的权限。
    """
    if context.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP):
        return
    try:
        with runtime.transaction():
            _ensure_image_access(runtime, context, observed_at)
    except Exception as error:
        logger.warning("[CompanionV2] 生图能力授权补齐失败 error_type=%s", type(error).__name__)


def _ensure_image_access(runtime, context, observed_at):
    repository = CapabilityRepository(runtime)
    role, scope, subject = context.role_binding.role, context.scope, context.actor.subject
    capabilities = IMAGE_CAPABILITIES
    if scope.kind is ScopeKind.GROUP:
        from companion_v2.conversation.media.output_settings import effective_settings, image_request_denial
        denial = image_request_denial(runtime, context, effective_settings(runtime, context))
        if denial is not None:
            return
        capabilities = GROUP_IMAGE_CAPABILITIES
    projected = repository.projection(extension_id=IMAGE_EXTENSION, role=role, scope=scope)
    if not projected.is_success:
        return
    projection = projected.unwrap()
    if projection.installation.desired_state is not CapabilityDesiredState.ACTIVE:
        return
    if projection.role_selection is not None:
        if not projection.role_selection.enabled:
            return
    else:
        result = repository.set_role_enabled(
            extension_id=IMAGE_EXTENSION, role=role, bot_id=scope.bot_id, enabled=True,
            expected_revision=0, observed_at=observed_at)
        if not result.is_success:
            logger.warning("[CompanionV2] 生图能力角色启用失败 code=%s", result.failure.code)
            return
    for capability in capabilities:
        consent = repository.consent(extension_id=IMAGE_EXTENSION, capability_id=capability,
                                     subject=subject, scope=scope)
        if consent.is_success or consent.failure.code != "capability_consent_missing":
            continue
        # AstrBot 新建对话或切换人格后 conversation_id 会变化。沿用同一用户、
        # bot、账号和私聊窗口最近的选择，避免把之前撤回的能力重新默认开启。
        previous = repository.latest_route_authorization(
            extension_id=IMAGE_EXTENSION, capability_id=capability, subject=subject, scope=scope)
        state = previous.state if previous is not None else CapabilityAuthorizationState.ACTIVE
        result = repository.set_authorization(
            extension_id=IMAGE_EXTENSION, capability_id=capability, subject=subject, scope=scope,
            state=state, expected_revision=0, observed_at=observed_at)
        if not result.is_success:
            logger.warning("[CompanionV2] 生图能力授权失败 capability=%s code=%s",
                           capability, result.failure.code)
    if any(repository.authorize(extension_id=IMAGE_EXTENSION, capability_id=capability,
                                subject=subject, scope=scope).is_success for capability in capabilities):
        _ensure_invocation_grant(runtime, context, observed_at)


def _ensure_invocation_grant(runtime, context, observed_at):
    """补上工具执行入口；每项生图能力仍由原生能力授权单独检查。"""
    identities = PersistentIdentityDirectory(runtime)
    scope, subject = context.scope, context.actor.subject
    grants = tuple(grant for grant in identities.grants_for_subject(subject)
                   if grant.capability == "extension.tool.invoke"
                   and replace(grant.scope, conversation_id=scope.conversation_id) == scope)
    # 已有授权、撤回、到期或尚未生效的决定均不覆盖。
    if any(grant.scope == scope for grant in grants):
        return
    latest = max(grants, key=lambda grant: (grant.revoked_at or grant.valid_from,
                                          grant.revision, grant.grant_id), default=None)
    if latest is not None and (latest.state is not GrantState.ACTIVE or latest.valid_from > observed_at
                              or latest.expires_at is not None and latest.expires_at <= observed_at):
        return
    identities.issue_grant(grant_id="image-access:" + digest([subject, scope]), subject=subject,
        scope=scope, capability="extension.tool.invoke", tier=AuthorizationTier.STANDARD,
        valid_from=observed_at, expires_at=latest.expires_at if latest is not None else None,
        expected_revision=identities.revision)


def provision_private_image_consent(runtime: StorageRuntime, context: TaskContext, *,
                                    observed_at: datetime) -> bool:
    """Last-resort consent provisioning for the current private conversation.

    ``ensure_image_access`` normally does this before the catalogue is built, but
    a conversation switch can still leave the exact-scope rows absent. This
    fallback only creates rows that are missing; an explicit revocation is never
    overwritten, and group windows keep their own role/consent gate.
    """
    if getattr(getattr(context, "scope", None), "kind", None) is not ScopeKind.PRIVATE:
        return False
    from companion_v2.capabilities.models import CapabilityAuthorizationState, CapabilityDesiredState
    try:
        repository = CapabilityRepository(runtime)
        installed = repository.installation(extension_id=IMAGE_EXTENSION)
        if not installed.is_success:
            return False
        if installed.unwrap().desired_state is not CapabilityDesiredState.ACTIVE:
            return False
        role, scope, subject = context.role_binding.role, context.scope, context.actor.subject
        projection = repository.projection(extension_id=IMAGE_EXTENSION, role=role, scope=scope)
        if not projection.is_success:
            return False
        selection = projection.unwrap().role_selection
        if selection is not None and not selection.enabled:
            return False
        created = False
        with runtime.transaction():
            for capability in IMAGE_CAPABILITIES:
                consent = repository.consent(extension_id=IMAGE_EXTENSION, capability_id=capability,
                                             subject=subject, scope=scope)
                if consent.is_success or consent.failure.code != "capability_consent_missing":
                    continue
                result = repository.set_authorization(
                    extension_id=IMAGE_EXTENSION, capability_id=capability, subject=subject, scope=scope,
                    state=CapabilityAuthorizationState.ACTIVE, expected_revision=0, observed_at=observed_at)
                if result.is_success:
                    created = True
                else:
                    logger.warning("[CompanionV2] 生图会话授权补齐失败 capability=%s code=%s",
                                   capability, result.failure.code)
        return created
    except Exception as error:
        logger.warning("[CompanionV2] 生图会话授权补齐异常 error_type=%s", type(error).__name__)
        return False
