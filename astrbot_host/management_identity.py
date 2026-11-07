"""Map a host-authenticated Dashboard administrator to a separate V2 human identity."""

from companion_v2.execution.models import TaskError, digest, require
from companion_v2.foundation.refs import SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory


def _authenticated_api_key(username):
    """Only the host's scoped extension dispatcher can supply a service identity."""
    if not isinstance(username, str) or not username.startswith("api_key:") or not username[8:]:
        return False
    try:
        from astrbot.api.web import request
        from starlette.requests import Request
        return (request.username == username and isinstance(request._request, Request)
                and request.path.startswith("/api/v1/plugins/extensions/"))
    except (RuntimeError, AttributeError):
        return False


def dashboard_operator(runtime, username, *, at):
    # username must come from astrbot.api.web.request, never a query/body/header claim.
    if (type(username) is not str or not username.strip() or username != username.strip()
            or len(username) > 128 or (username.startswith("api_key:") and not _authenticated_api_key(username))):
        raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")
    identifier = "dashboard:" + digest([runtime.installation_id, username])
    service = username.startswith("api_key:")
    principal = AuthenticatedHostPrincipal(runtime.installation_id,
                                            "astrbot-dashboard-api-key" if service else "astrbot-dashboard",
                                            "dashboard", username)
    identities = PersistentIdentityDirectory(runtime)
    # 先读一次再决定要不要写。原先每个 Dashboard 请求都先开一个写事务
    # （BEGIN IMMEDIATE），即使这个人早就绑定过、这一笔根本不写任何行：
    # 在会话繁忙时这个空事务也要排进 V2 的全局 SQLite 锁，把一次纯读取
    # 变成「等后台写事务」。只有真的缺少绑定时才进入写事务。
    resolved = identities.resolve_authenticated(principal, observed_at=at)
    if resolved.is_success:
        return resolved.unwrap()
    if resolved.failure is not None and resolved.failure.code != "unbound_identity":
        # A known revoked binding or downgraded tier is never silently restored.
        return require(resolved)
    with runtime.transaction():
        # 事务内重新读：首绑并发时后到的一方要看到先到方刚写入的绑定。
        resolved = identities.resolve_authenticated(principal, observed_at=at)
        if resolved.failure is not None and resolved.failure.code == "unbound_identity":
            subject = SubjectRef(runtime.installation_id, SubjectKind.SERVICE if service else SubjectKind.HUMAN, identifier)
            identities.bind(binding_id=identifier, principal=principal, subject=subject,
                valid_from=at, expected_revision=identities.revision)
            policy = ObjectAccessPolicy(runtime)
            policy.set_tier(subject, AuthorizationTier.CORE, expected_revision=policy.revision, observed_at=at)
            resolved = identities.resolve_authenticated(principal, observed_at=at)
        # A known revoked binding or downgraded tier is never silently restored.
        return require(resolved)
