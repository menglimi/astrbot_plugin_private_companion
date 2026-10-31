"""Explicit, per-action consent for native Reality capabilities."""

from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.identity_policy.repository import PersistentIdentityDirectory
from companion_v2.storage.codec import scope_values
from companion_v2.storage.models import OperationState
from companion_v2.storage.repositories import OperationRepository

from .models import CapabilityAuthorizationState, CapabilityDesiredState
from .privacy import is_core
from .repositories import CapabilityRepository


ACTION = "reality.snapshot.read"
EXTENSION = "reality"
PACKAGE_ID = "astrbot_plugin_private_companion.reality"
OWNER = "capability.reality-access"


class RealitySnapshotAccess:
    def __init__(self, application, context, package, *, capability=ACTION):
        self.application, self.context, self.package = application, context, package
        self.capability = capability
        self.runtime = application.runtime
        self.repository = CapabilityRepository(self.runtime)
        self.identities = PersistentIdentityDirectory(self.runtime)
        self.policy = ObjectAccessPolicy(self.runtime)
        if (package.extension_id != EXTENSION
                or package.package_id != PACKAGE_ID
                or capability not in package.capabilities):
            raise TaskError("reality_provider_changed", "the loaded Reality package changed")

    def _access(self, operator, at, *, action="capability.view"):
        context = self.context
        # 核心管理员默认可用：会话范围不再限制现实触及能力。
        if is_core(self.runtime, operator.subject):
            return
        require(self.application.tasks.authorize_context(context, action="conversation.reply", observed_at=at))
        require(self.policy.authorize(operator, scope=context.scope, action=action,
                                      owner=context.actor.subject, observed_at=at))

    def _state(self):
        context = self.context
        installation = self.repository.installation(extension_id=EXTENSION)
        installed = installation.unwrap() if installation.is_success else None
        selection = None
        if installed is not None:
            selection = require(self.repository.projection(extension_id=EXTENSION,
                role=context.role_binding.role, scope=context.scope)).role_selection
        consent = self.runtime.read_one("""SELECT state, revision FROM capability_authorizations
            WHERE extension_id = ? AND capability_id = ? AND subject_kind = ? AND subject_id = ?
              AND installation_id = ? AND platform = ? AND scope_kind = ? AND native_scope_id = ?
              AND bot_id = ? AND account_id = ? AND conversation_id = ? AND session_id = ?""",
            (EXTENSION, self.capability, context.actor.subject.kind.value, context.actor.subject.subject_id,
             *scope_values(context.scope)))
        return installed, selection, dict(consent) if consent is not None else None

    def _token(self, state):
        return digest([self.capability, self.context.key, encode(self.package),
                       [encode(item) if item is not None else None for item in state],
                       self.identities.revision, self.policy.revision])

    def status(self, operator, at):
        self._access(operator, at)
        with self.runtime.read_snapshot():
            state = self._state()
            installed, selection, consent = state
            same = installed is not None and (
                installed.package_id, installed.package_version, installed.artifact_hash
            ) == (self.package.package_id, self.package.package_version, self.package.artifact_hash)
            active = bool(same and installed.desired_state is CapabilityDesiredState.ACTIVE)
            role_enabled = bool(selection and selection.enabled)
            core = is_core(self.runtime, self.context.actor.subject)
            consented = bool(core or
                             (consent and consent["state"] == CapabilityAuthorizationState.ACTIVE.value))
            can_manage = bool(is_core(self.runtime, operator.subject) or
                self.policy.scope_access(operator, scope=self.context.scope,
                    action="capability.role.set", observed_at=at, core_only=True).is_success)
            external = self.application.external
            invokable = bool(external and any(tool.action == self.capability
                             for tool in external.catalog_for(self.context)))
            reason = ("capability_not_installed" if installed is None else
                      "reality_package_changed" if not same else
                      "capability_not_active" if not active else
                      "capability_role_disabled" if not role_enabled else
                      "camera_consent_missing" if self.capability != ACTION and not consented else
                      "capability_consent_missing" if not consented else
                      "camera_tool_unavailable" if self.capability != ACTION and not invokable else
                      "reality_tool_unavailable" if not invokable else "")
            return {"action": self.capability, "package_version": self.package.package_version,
                    "installed": installed is not None, "active": active,
                    "role_enabled": role_enabled, "role_revision": selection.revision if selection else 0,
                    "consented": consented, "auto_consent": core,
                    "invokable": invokable, "can_manage": can_manage, "reason": reason,
                    "edit_token": self._token(state)}

    def configure(self, operator, command, at):
        self._access(operator, at, action="capability.consent.set")
        # Package selection and role enablement also require the authenticated
        # administrator; a device upload or model call cannot grant these rights.
        require(self.policy.scope_access(operator, scope=self.context.scope,
            action="capability.role.set", observed_at=at, core_only=True))
        if (type(command) is not dict or set(command) != {"operation_key", "edit_token", "enabled"}
                or type(command["enabled"]) is not bool
                or any(type(command[key]) is not str or not command[key].strip()
                       or len(command[key]) > 160 for key in ("operation_key", "edit_token"))):
            raise TaskError("reality_request_invalid", "an explicit scoped access command is required")
        context = self.context
        key = digest([context.key, operator.subject.subject_id, command["operation_key"]])
        operation = OperationRef("reality-access:" + key, OWNER, context.scope,
                                 VersionRef(key, 1), key)
        operations = OperationRepository(self.runtime)
        with self.runtime.transaction():
            receipt = require(operations.reserve(operation,
                parameter_hash=digest([encode(operator.principal), encode(operator.subject),
                    operator.binding_id, operator.binding_revision, context.key, command]), observed_at=at))
            if receipt.replayed:
                if receipt.state is not OperationState.COMPLETED:
                    raise TaskError("capability_operation_unresolved", "the original access command is unresolved")
                return {"replayed": True, "status": self.status(operator, at)}
            state = self._state()
            if command["edit_token"] != self._token(state):
                raise TaskError("reality_access_changed", "refresh the changed access settings before saving")
            installed, selection, consent = state
            enabled = command["enabled"]
            if not enabled and (installed is None or installed.desired_state is CapabilityDesiredState.DISCARDED):
                raise TaskError("capability_not_installed", "the Reality extension has not been assembled")
            if enabled:
                require(self.repository.register_descriptor(self.package, registered_at=at))
                if installed is None:
                    installed = require(self.repository.acquire(extension_id=EXTENSION,
                        package_id=self.package.package_id, package_version=self.package.package_version,
                        expected_revision=0, observed_at=at))
                elif (installed.package_id, installed.package_version, installed.artifact_hash) != (
                        self.package.package_id, self.package.package_version, self.package.artifact_hash):
                    raise TaskError("reality_package_changed", "the selected package differs from the loaded package")
                if installed.desired_state is not CapabilityDesiredState.ACTIVE:
                    raise TaskError("capability_not_active", "the selected Reality package is paused or discarded")
                if not selection or not selection.enabled:
                    require(self.repository.set_role_enabled(extension_id=EXTENSION,
                        role=context.role_binding.role, bot_id=context.scope.bot_id, enabled=True,
                        expected_revision=selection.revision if selection else 0, observed_at=at))
                if not self.identities.authorize(context.actor, scope=context.scope,
                        capability="extension.tool.invoke", observed_at=at).is_success:
                    binding = next(item for item in self.identities.snapshot().bindings
                                   if item.binding_id == context.actor.binding_id)
                    self.identities.issue_grant(grant_id="reality-invoke:" + key,
                        subject=context.actor.subject, scope=context.scope, capability="extension.tool.invoke",
                        tier=AuthorizationTier.STANDARD, valid_from=at, expires_at=binding.expires_at,
                        expected_revision=self.identities.revision)
            # Revocation affects this participant and complete conversation scope.
            # Other users' consent and the shared Bot/role selection remain intact.
            if installed is not None:
                require(self.repository.set_authorization(extension_id=EXTENSION, capability_id=self.capability,
                    subject=context.actor.subject, scope=context.scope,
                    state=CapabilityAuthorizationState.ACTIVE if enabled else CapabilityAuthorizationState.REVOKED,
                    expected_revision=consent["revision"] if consent else 0, observed_at=at))
            require(operations.transition(operation.operation_id, expected_state=OperationState.ACCEPTED,
                next_state=OperationState.COMPLETED, observed_at=at))
            status = self.status(operator, at)
            if enabled and not status["invokable"]:
                raise TaskError("reality_tool_unavailable", "Reality was not admitted to this conversation's tool catalog")
            return {"replayed": False, "status": status}
