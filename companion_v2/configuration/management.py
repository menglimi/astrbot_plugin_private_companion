"""Authorized editors for the image, reaction and budget settings the cottage shows.

Each group here already had a registered owner, a real field contract and a live
consumer; what they did not have was a dashboard read-write path, so the only way
to change them was the AstrBot plugin form. These editors add that path without
adding a second save mechanism: every write goes through
``ConfigurationService.patch`` with the same owner, layer, revision and
idempotency rules as the rest of the cottage.
"""

from companion_v2.configuration.models import ApplyMode, ConfigLayer, ConfigTarget, ConfigurationError, fingerprint
from companion_v2.execution.models import TaskError, digest, require
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import OperationRef, ScopeKind, VersionRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from .contract import contract_of, layers_of, provenance_of

from ..conversation.media.quota import (GROUP as QUOTA_GROUP, OWNER as QUOTA_OWNER,
    configuration_fields as quota_fields)
from ..conversation.media.review import (GROUP as REVIEW_GROUP, OWNER as REVIEW_OWNER,
    configuration_fields as review_fields)
from ..conversation.media.output_settings import (GROUP as IMAGE_OUTPUT_GROUP, OWNER as IMAGE_OUTPUT_OWNER)
from ..conversation.busy_gate import (GROUP as BUSY_GROUP, OWNER as BUSY_OWNER,
    configuration_fields as busy_fields)
from ..conversation.participation import (CONFIG_GROUP as PARTICIPATION_GROUP,
    CONFIG_OWNER as PARTICIPATION_OWNER, configuration_fields as participation_fields)
from ..conversation.recall_settings import (GROUP as RECALL_GROUP, OWNER as RECALL_OWNER,
    configuration_fields as recall_fields)
from ..conversation.web_understanding import (GROUP as WEB_GROUP, OWNER as WEB_OWNER,
    configuration_fields as web_fields)
from ..conversation.relay_settings import (GROUP as RELAY_GROUP, OWNER as RELAY_OWNER,
    configuration_fields as relay_fields)
from ..expression.reaction_configuration import (GROUP as REACTION_GROUP, OWNER as REACTION_OWNER,
    configuration_fields as reaction_fields)
from ..expression.configuration import (GROUP as EXPRESSION_GROUP, OWNER as EXPRESSION_OWNER,
    PRIVATE_GROUP as EXPRESSION_PRIVATE_GROUP, PRIVATE_OWNER as EXPRESSION_PRIVATE_OWNER,
    configuration_fields as expression_fields,
    private_configuration_fields as expression_private_fields,
    private_target_for as expression_private_target_for)
from ..memory.person.configuration import (GROUP as PERSON_GROUP, LEARNING_GROUP as PERSON_LEARNING_GROUP,
    configuration_fields as person_fields,
    legacy_window_snapshot as legacy_person_window_snapshot,
    legacy_window_fields as legacy_person_window_fields,
    legacy_window_target_for as legacy_person_window_target_for,
    relationship_target_for as person_relationship_target_for)
from ..relationships.private_access import (GROUP as PRIVATE_ACCESS_GROUP,
    configuration_fields as private_access_fields)
from ..knowledge.configuration import (GROUP as KNOWLEDGE_GROUP, OWNER as KNOWLEDGE_OWNER,
    configuration_fields as knowledge_fields)
from ..model_gateway.budget import GROUP as BUDGET_GROUP, OWNER as BUDGET_OWNER, SOFT_FIELDS
from ..role_work.growth.configuration import (GROUP as GROWTH_GROUP, OWNER as GROWTH_OWNER,
    SKILLS_GROUP, SKILLS_OWNER, configuration_fields as growth_fields,
    skill_configuration_fields)
from ..role_profile.configuration import (FIELD_LIMITS as ROLE_PROFILE_FIELD_LIMITS,
    GROUP as ROLE_PROFILE_GROUP, OWNER as ROLE_PROFILE_OWNER,
    configuration_fields as role_profile_fields)
from ..system_prompts.configuration import (GROUP as SYSTEM_PROMPT_GROUP,
    MAX_PROMPT_CHARS, OWNER as SYSTEM_PROMPT_OWNER)
from ..relationships.profile import (FIELD_LIMITS as RELATIONSHIP_PROFILE_FIELD_LIMITS,
    GROUP as RELATIONSHIP_PROFILE_GROUP, OWNER as RELATIONSHIP_PROFILE_OWNER,
    configuration_fields as relationship_profile_fields,
    target_for as relationship_profile_target_for)
from ..role_work.life.configuration import (GROUP as LIFE_GROUP, OWNER as LIFE_OWNER, SLEEP_FIELDS,
    configuration_fields as life_fields)
from ..screen.configuration import (GROUP as SCREEN_GROUP, OWNER as SCREEN_OWNER,
    configuration_fields as screen_fields)
from ..qzone.configuration import (GROUP as QZONE_GROUP, OWNER as QZONE_OWNER,
    configuration_fields as qzone_fields)
from ..library.configuration import (GROUP as LIBRARY_GROUP, OWNER as LIBRARY_OWNER,
    configuration_fields as library_fields)
from ..references.configuration import (GROUP as REFERENCE_GROUP, OWNER as REFERENCE_OWNER,
    configuration_fields as reference_fields)
from ..references.wardrobe import (GROUP as WARDROBE_GROUP, OWNER as WARDROBE_OWNER,
    configuration_fields as wardrobe_fields)
from ..proactive.configuration import (GROUP as CONTACT_GROUP, WINDOW_QUOTA_FIELDS,
    configuration_fields as contact_fields)
from ..conversation.content.authoring_service import (GROUP as AUTHORING_GROUP,
    configuration_fields as authoring_fields)

from .service import ConfigurationService


class GroupEditor:
    """One configuration group's dashboard contract.

    ``layers`` is deliberately the group's own layer tuple. Single-layer groups
    are edited at that layer; inherited groups prefer role defaults unless they
    name an explicit target. Object pages keep their own window-level overrides.
    """

    group = ""
    owner = ""
    layers = (ConfigLayer.WINDOW,)
    target_layer = None
    operation_prefix = None
    error_prefix = "configuration"

    def __init__(self, app, context):
        self.app, self.context, self.runtime = app, context, app.runtime
        self.policy = ObjectAccessPolicy(self.runtime)

    @property
    def short_names(self):
        return {field.field_id.removeprefix(self.group + ".") for field in self.app.configuration.registry.group(self.group)
                if field.field_id.startswith(self.group + ".")}

    def _target(self):
        layer = self._target_layer()
        if layer is ConfigLayer.INSTALLATION:
            return ConfigTarget(self.context.scope.installation_id, ConfigLayer.INSTALLATION)
        if layer is ConfigLayer.WINDOW:
            # A registered private scope already identifies its participant. Keep
            # ordinary window settings on the same key their runtime readers use;
            # relationship-aware groups override this with an explicit subject.
            return ConfigTarget(self.context.scope.installation_id, ConfigLayer.WINDOW,
                                self.context.role_binding.role, self.context.scope)
        if layer is ConfigLayer.RELATIONSHIP:
            raise RuntimeError(f"{self.group} must provide a relationship target")
        return ConfigTarget(self.context.scope.installation_id, ConfigLayer.ROLE,
                            self.context.role_binding.role, bot_id=self.context.scope.bot_id)

    def _target_layer(self):
        layer = self.target_layer
        if layer is None:
            layer = (self.layers[0] if len(self.layers) == 1 else
                     ConfigLayer.ROLE if ConfigLayer.ROLE in self.layers else
                     ConfigLayer.WINDOW if ConfigLayer.WINDOW in self.layers else self.layers[0])
        if layer not in self.layers:
            raise RuntimeError(f"{self.group} target layer is not writable")
        return layer

    def _code(self, suffix):
        return f"{self.error_prefix}_{suffix}"

    def _authorize(self, operator, action):
        now, context = self.app.now(), self.context
        target_layer = self._target_layer()
        if target_layer is ConfigLayer.WINDOW:
            # A window-scoped group belongs to one original participant; the
            # binding is re-read here so a stale page cannot write to a new role.
            if context.scope.kind is not ScopeKind.PRIVATE or context.audience.recipients != (context.actor.subject,):
                raise TaskError(self._code("scope_invalid"), "select one original private participant")
            current = require(self.app.tasks.identity.resolve_authenticated(context.actor.principal, observed_at=now))
            binding = require(self.app.tasks.identity.resolve_role(context.scope, observed_at=now))
            if (current.subject != context.actor.subject or current.binding_id != context.actor.binding_id
                    or current.binding_revision != context.actor.binding_revision or binding != context.role_binding):
                raise TaskError(self._code("target_changed"), "the selected participant or role binding changed")
            self.app.tasks._context(context, now)
        target = self._target()
        scope = (target.authorization_scope if target_layer is ConfigLayer.INSTALLATION else
                 target.scope if target.scope is not None else context.scope)
        require(self.policy.scope_access(operator, scope=scope, action=action, observed_at=now, core_only=True))
        return target

    def _settings(self, snapshot):
        """Short names only: the page never sees a fully qualified field id."""
        prefix = self.group + "."
        return {key.removeprefix(prefix): value for key, value in snapshot.values.items() if key.startswith(prefix)}

    def _definitions(self):
        prefix = self.group + "."
        return {field.field_id.removeprefix(prefix): field
                for field in self.app.configuration.registry.group(self.group)
                if field.field_id.startswith(prefix)}

    def _layers(self):
        """The inheritance chain this editor writes into, outermost first.

        ``ConfigTarget.chain`` always starts at installation, so the page can show
        which of these layers a value actually came from instead of collapsing the
        whole chain into one "继承" label.
        """
        return layers_of(self._target())

    def _provenance(self, snapshot):
        """Per field: where the effective value came from, and whether this layer set it."""
        return provenance_of(snapshot, self.group)

    def _field_contract(self):
        """Bounds and kinds the workbench renders controls from."""
        return contract_of(self.app.configuration.registry, self.group)

    def _after_configure(self, operator, data, result):
        """Apply group-owned side effects inside the configuration transaction."""

    @outcome
    def status(self, operator):
        with self.runtime.read_snapshot():
            target = self._authorize(operator, "configuration.read")
            snapshot = require(self.app.configuration.read(operator, target=target, group_id=self.group,
                                                           observed_at=self.app.now()))
            definitions = self._definitions()
            modes = {field.apply_mode for field in definitions.values()}
            return {"settings": self._settings(snapshot), "edit_token": snapshot.edit_token,
                    "revision": snapshot.revision, "configuration_token": snapshot.resolution_token,
                    "group_id": self.group,
                    "scope": target.layer.value,
                    "apply_mode": "next_task" if ApplyMode.NEXT_TASK in modes else "immediate",
                    # Layer provenance: which node of the chain each value came from.
                    "layers": self._layers(), "provenance": self._provenance(snapshot),
                    "fields": self._field_contract()}

    @outcome
    def configure(self, operator, data):
        if (type(data) is not dict or not {"operation_key", "edit_token", "changes"} <= set(data)
                or set(data) - {"operation_key", "edit_token", "changes", "reset_to_inherit"}
                or type(data["operation_key"]) is not str or not data["operation_key"].strip()
                or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                or len(data["edit_token"]) != 64 or type(data["changes"]) is not dict
                or (not data["changes"] and not data.get("reset_to_inherit"))
                or ("reset_to_inherit" in data and (type(data["reset_to_inherit"]) is not list
                    or any(type(name) is not str for name in data["reset_to_inherit"])))
                or any(type(key) is not str for key in data["changes"])):
            raise TaskError(self._code("configuration_invalid"), "provide explicit setting changes and an edit token")
        resets = data.get("reset_to_inherit", [])
        if (set(data["changes"]).union(resets) - self.short_names
                or set(data["changes"]).intersection(resets) or len(set(resets)) != len(resets)):
            raise TaskError(self._code("configuration_invalid"), "unknown setting for this group")
        with self.runtime.transaction():
            target = self._authorize(operator, "configuration.write")
            require(self.app.tasks.check_generation())
            op_id = (self.operation_prefix or f"{self.group}-config:") + digest(
                [operator.subject, target.key, data["operation_key"]])
            try:
                receipt = require(self.app.configuration.patch(operator, target=target, group_id=self.group,
                    operation=OperationRef(op_id, self.owner, target.authorization_scope,
                                           VersionRef(op_id, 1), op_id),
                    expected_edit_token=data["edit_token"],
                    changes={self.group + "." + name: value for name, value in data["changes"].items()},
                    reset_to_inherit=tuple(self.group + "." + name for name in resets),
                    observed_at=self.app.now()))
            except ConfigurationError as error:
                raise TaskError(error.code, str(error)) from error
            result = {"replayed": receipt.replayed, "operation_id": op_id}
            self._after_configure(operator, data, result)
        self.app.notify()
        return result


class ImageOutputManagement(GroupEditor):
    group = IMAGE_OUTPUT_GROUP
    owner = IMAGE_OUTPUT_OWNER
    layers = (ConfigLayer.INSTALLATION, ConfigLayer.ROLE)
    error_prefix = "image_output"


class ImageQuotaManagement(GroupEditor):
    """Daily image allowances: an installation/role ceiling, not a window one."""

    group, owner = QUOTA_GROUP, QUOTA_OWNER
    layers = quota_fields()[0].layers
    error_prefix = "image_quota"


class ImageReviewManagement(GroupEditor):
    """Role defaults for image review, with exact window overrides on object pages."""

    group, owner = REVIEW_GROUP, REVIEW_OWNER
    layers = review_fields()[0].layers
    error_prefix = "image_review"


class BusyGateManagement(GroupEditor):
    """The busy reply gate: whether a reply waits for a quiet window, and how long.

    Role defaults apply across its conversations; object pages can adjust their
    own wait behavior independently.
    """

    group, owner = BUSY_GROUP, BUSY_OWNER
    layers = busy_fields()[0].layers
    error_prefix = "busy_gate"


class ReactionRetrievalManagement(GroupEditor):
    """Installation-wide reaction retrieval settings for the shared role catalogs."""

    group, owner = REACTION_GROUP, REACTION_OWNER
    layers = reaction_fields()[0].layers
    target_layer = ConfigLayer.INSTALLATION
    error_prefix = "reaction_configuration"


class ModelBudgetManagement(GroupEditor):
    """Installation-wide optional-work controls; role limits cannot enable the master."""

    group, owner = BUDGET_GROUP, BUDGET_OWNER
    layers = SOFT_FIELDS[0].layers
    target_layer = ConfigLayer.INSTALLATION
    error_prefix = "model_budget"


class RecallManagement(GroupEditor):
    """Reading back a participant's just-retracted inbound text, window scoped."""

    group, owner = RECALL_GROUP, RECALL_OWNER
    layers = recall_fields()[0].layers
    error_prefix = "recall"


class WebUnderstandingManagement(GroupEditor):
    """Reading a page the participant linked, window scoped."""

    group, owner = WEB_GROUP, WEB_OWNER
    layers = web_fields()[0].layers
    error_prefix = "web_understanding"


class RelayManagement(GroupEditor):
    """Cross-window relaying: who may send where, and whether it needs confirmation."""

    group, owner = RELAY_GROUP, RELAY_OWNER
    layers = relay_fields()[0].layers
    error_prefix = "relay"


class ScreenManagement(GroupEditor):
    """Whether screen captures may be interpreted and reused as diary material."""

    group, owner = SCREEN_GROUP, SCREEN_OWNER
    layers = screen_fields()[0].layers
    error_prefix = "screen"


class QzoneManagement(GroupEditor):
    """Which public Zone actions the role may take, and how fast.

    The group is role-shared like ``role.life``: the Zone account belongs to the
    role, so ``RoleSharedSettingsOwner`` keeps a window value only as the legacy
    fallback and the editor just names the group.
    """

    group, owner = QZONE_GROUP, QZONE_OWNER
    layers = qzone_fields()[0].layers
    error_prefix = "qzone"


class ContentAuthoringManagement(GroupEditor):
    """Role-level direction and availability for native Content creation."""

    group = AUTHORING_GROUP
    owner = ConfigurationService.OWNER
    layers = authoring_fields()[0].layers
    error_prefix = "content_authoring"


class AcquaintanceManagement(GroupEditor):
    """Role defaults for acquaintance cards, shared by that role's contacts."""

    group = PERSON_GROUP
    owner = ConfigurationService.OWNER
    layers = (ConfigLayer.ROLE,)
    error_prefix = "person"

    def _field_contract(self):
        contract = super()._field_contract()
        for definition in contract.values():
            definition["editable_layers"] = [ConfigLayer.ROLE.value]
        return contract


class PrivateAcquaintanceSettingsMixin:
    """Stable per-person overrides, with read-only compatibility for old window rows."""

    legacy_fields = {}

    def _target(self):
        return person_relationship_target_for(self.context)

    def _authorize(self, operator, action):
        now, context = self.app.now(), self.context
        if (context.scope.kind is not ScopeKind.PRIVATE
                or context.audience.recipients != (context.actor.subject,)
                or context.actor.subject.kind.value != "human"):
            raise TaskError(self._code("scope_invalid"), "select one resolved private person")
        current = require(self.app.tasks.identity.resolve_authenticated(
            context.actor.principal, observed_at=now))
        binding = require(self.app.tasks.identity.resolve_role(context.scope, observed_at=now))
        if (current.subject != context.actor.subject
                or current.binding_id != context.actor.binding_id
                or current.binding_revision != context.actor.binding_revision
                or binding != context.role_binding):
            raise TaskError(self._code("target_changed"),
                            "the selected person or role binding changed")
        target = self._target()
        require(self.policy.scope_access(operator, scope=target.authorization_scope,
            action=action, observed_at=now, owner=context.actor.subject, core_only=True))
        return target

    @outcome
    def status(self, operator):
        result = require(super().status(operator))
        legacy = legacy_person_window_snapshot(self.runtime, self.context)
        if self.group == PERSON_GROUP:
            role_target = ConfigTarget(self.context.scope.installation_id, ConfigLayer.ROLE,
                self.context.role_binding.role, bot_id=self.context.scope.bot_id)
            inherited = require(self.app.configuration.read(operator, target=role_target,
                group_id=PERSON_GROUP, observed_at=self.app.now()))
            result["inherited_settings"] = {
                key.removeprefix(PERSON_GROUP + "."): value
                for key, value in inherited.values.items()
            }
        legacy_values = {}
        for name, legacy_id in self.legacy_fields.items():
            value = legacy.fields[legacy_id]
            if not result["provenance"][name]["explicit"] and value.explicit:
                result["settings"][name] = value.value
                result["provenance"][name] = {
                    "layer": ConfigLayer.WINDOW.value, "explicit": True,
                    "revision": value.source_revision, "legacy": True,
                }
            if value.explicit:
                legacy_values[legacy_id] = [value.value, value.source_revision]
        result["configuration_edit_token"] = result["edit_token"]
        result["edit_token"] = fingerprint([result["configuration_edit_token"], legacy.revision, legacy_values])
        return result

    @outcome
    def configure(self, operator, data):
        with self.runtime.transaction():
            status = require(self.status(operator))
            if type(data) is not dict or data.get("edit_token") != status["edit_token"]:
                raise TaskError("configuration_revision_conflict",
                                "configuration, inheritance, binding, or legacy settings changed")
            result = require(super().configure(operator,
                {**data, "edit_token": status["configuration_edit_token"]}))
            reset_names = data.get("reset_to_inherit", [])
            legacy_reset = tuple(self.legacy_fields[name] for name in reset_names
                                 if name in self.legacy_fields)
            if legacy_reset:
                from companion_v2.configuration.owners import SparseConfigurationOwner
                legacy = legacy_person_window_snapshot(self.runtime, self.context)
                SparseConfigurationOwner(self.runtime).write(
                    legacy_person_window_target_for(self.context), legacy_person_window_fields(),
                    changes={}, reset=legacy_reset, expected_revision=legacy.revision,
                    observed_at=self.app.now())
            return result

    def _field_contract(self):
        contract = super()._field_contract()
        for definition in contract.values():
            definition["editable_layers"] = [ConfigLayer.RELATIONSHIP.value]
        return contract


class PrivateAcquaintanceManagement(PrivateAcquaintanceSettingsMixin, AcquaintanceManagement):
    """Per-person card overrides, inherited from the role's acquaintance defaults."""

    layers = person_fields()[0].layers
    target_layer = ConfigLayer.RELATIONSHIP
    operation_prefix = "person-relationship-config:"
    legacy_fields = {
        "acquaintance_enabled": "person.acquaintance_enabled",
        "acquaintance_card_chars": "person.acquaintance_card_chars",
    }


class PrivateAcquaintanceLearningManagement(PrivateAcquaintanceSettingsMixin, GroupEditor):
    """Per-person learning consent, independent of role-wide card defaults."""

    group = PERSON_LEARNING_GROUP
    owner = ConfigurationService.OWNER
    layers = (ConfigLayer.RELATIONSHIP,)
    target_layer = ConfigLayer.RELATIONSHIP
    error_prefix = "person"
    operation_prefix = "person-learning-relationship-config:"
    legacy_fields = {
        "acquaintance_learning_enabled": "person.acquaintance_learning_enabled",
    }

    def _field_contract(self):
        contract = GroupEditor._field_contract(self)
        for definition in contract.values():
            definition["editable_layers"] = [ConfigLayer.RELATIONSHIP.value]
        return contract


class PrivateAccessManagement(GroupEditor):
    """Role defaults for known private users and their refusal line.

    这一组**不是**按人开关：每个人的对话/主动权限始终是 grant
    （``conversation.reply`` / ``proactive.contact``），由关系页按人读写。
    这里只回答两件事——没人决定过的新用户从哪开始、没有对话权限时回哪一句。
    因此它的层只有安装与角色：默认值不该跟着某一个对话窗口变。
    """

    group = PRIVATE_ACCESS_GROUP
    owner = ConfigurationService.OWNER
    layers = private_access_fields()[0].layers
    error_prefix = "private_access"
    visible_fields = frozenset({"dialogue_default", "proactive_default",
                                "denied_reply", "notice_cooldown_seconds"})

    @property
    def short_names(self):
        return self.visible_fields

    def _settings(self, snapshot):
        return {key: value for key, value in super()._settings(snapshot).items()
                if key in self.visible_fields}

    def _definitions(self):
        return {key: value for key, value in super()._definitions().items()
                if key in self.visible_fields}

    def _provenance(self, snapshot):
        return {key: value for key, value in super()._provenance(snapshot).items()
                if key in self.visible_fields}

    def _field_contract(self):
        return {key: value for key, value in super()._field_contract().items()
                if key in self.visible_fields}


class StrangerPrivateAccessManagement(PrivateAccessManagement):
    """Installation defaults read by unbound private accounts."""

    target_layer = ConfigLayer.INSTALLATION
    visible_fields = frozenset({"denied_reply", "notice_cooldown_seconds",
                                "stranger_notice_enabled", "stranger_notice_daily_cap"})


class RoleGrowthManagement(GroupEditor):
    """Role growth: reading habits, proposals and how strongly they steer plans."""

    group, owner = GROWTH_GROUP, GROWTH_OWNER
    layers = growth_fields()[0].layers
    error_prefix = "role_growth"


class LibraryManagement(GroupEditor):
    """The reading shelf: catalog, review and reading options for one role.

    The shelf belongs to a role body rather than one conversation window, so the
    fields carry role scope and a window cannot narrow them on its own.
    """

    group, owner = LIBRARY_GROUP, LIBRARY_OWNER
    layers = library_fields()[0].layers
    error_prefix = "library"


class KnowledgeManagement(GroupEditor):
    """World-book and knowledge-base import, plus how much of it reaches a turn.

    Two fields stay silent when wrong rather than refusing: ``import_paths`` is
    resolved against the AstrBot data root and ``source_ids`` only accepts
    ``kb:``/``doc:`` ids that exist in this installation, so a value that does
    not match simply imports nothing.
    """

    group, owner = KNOWLEDGE_GROUP, KNOWLEDGE_OWNER
    layers = knowledge_fields()[0].layers
    error_prefix = "knowledge"


class RoleGrowthSkillsManagement(GroupEditor):
    """Role skill domains and how strongly they steer plans."""

    group, owner = SKILLS_GROUP, SKILLS_OWNER
    layers = (ConfigLayer.ROLE,)
    error_prefix = "role_growth_skills"

    def _field_contract(self):
        contract = super()._field_contract()
        for definition in contract.values():
            definition["editable_layers"] = [ConfigLayer.ROLE.value]
        return contract


class ExpressionLearningManagement(GroupEditor):
    """Role-level learning policy; relationship and group exceptions stay separate."""

    group, owner = EXPRESSION_GROUP, EXPRESSION_OWNER
    layers = expression_fields()[0].layers
    error_prefix = "expression_learning"


class PrivateExpressionLearningManagement(GroupEditor):
    """The learning opt-in belongs to a stable private relationship, not a window."""

    group, owner = EXPRESSION_PRIVATE_GROUP, EXPRESSION_PRIVATE_OWNER
    layers = expression_private_fields()[0].layers
    error_prefix = "expression_learning_private"

    def _target(self):
        return expression_private_target_for(self.context)

    def _authorize(self, operator, action):
        now, context = self.app.now(), self.context
        if (context.scope.kind is not ScopeKind.PRIVATE
                or context.audience.recipients != (context.actor.subject,)
                or context.actor.subject.kind.value != "human"):
            raise TaskError(self._code("scope_invalid"), "select one resolved private person")
        current = require(self.app.tasks.identity.resolve_authenticated(
            context.actor.principal, observed_at=now))
        binding = require(self.app.tasks.identity.resolve_role(context.scope, observed_at=now))
        if (current.subject != context.actor.subject or current.binding_id != context.actor.binding_id
                or current.binding_revision != context.actor.binding_revision
                or binding != context.role_binding):
            raise TaskError(self._code("target_changed"), "the selected person or role binding changed")
        target = self._target()
        require(self.policy.scope_access(operator, scope=context.scope, action=action,
            observed_at=now, owner=context.actor.subject, core_only=True))
        return target

    @outcome
    def status(self, operator):
        result = require(super().status(operator))
        field = EXPRESSION_PRIVATE_GROUP + ".private_learning_enabled"
        if not result["provenance"]["private_learning_enabled"]["explicit"]:
            shared = require(ExpressionLearningManagement(self.app, self.context).status(operator))
            result["settings"]["private_learning_enabled"] = shared["settings"]["private_learning_enabled"]
        return result


class RoleProfileManagement(GroupEditor):
    """Stable identity, personality and world canon shared by the selected role."""

    group, owner = ROLE_PROFILE_GROUP, ROLE_PROFILE_OWNER
    layers = role_profile_fields()[0].layers
    error_prefix = "role_profile"

    def _field_contract(self):
        contract = super()._field_contract()
        for name, maximum in ROLE_PROFILE_FIELD_LIMITS.items():
            contract[name]["maximum_chars"] = maximum
        return contract


class SystemPromptManagement(GroupEditor):
    """Fixed system prompt blocks, editable per role with restore-to-default."""

    group, owner = SYSTEM_PROMPT_GROUP, SYSTEM_PROMPT_OWNER
    layers = (ConfigLayer.ROLE,)
    error_prefix = "system_prompt"

    def _field_contract(self):
        contract = super()._field_contract()
        for entry in contract.values():
            entry["maximum_chars"] = MAX_PROMPT_CHARS
        return contract


class RelationshipProfileManagement(GroupEditor):
    """Operator-managed relationship details for one role and one private person."""

    group, owner = RELATIONSHIP_PROFILE_GROUP, RELATIONSHIP_PROFILE_OWNER
    layers = relationship_profile_fields()[0].layers
    error_prefix = "relationship_profile"

    def _target(self):
        return relationship_profile_target_for(self.context)

    def _authorize(self, operator, action):
        now, context = self.app.now(), self.context
        if (context.scope.kind is not ScopeKind.PRIVATE
                or context.audience.recipients != (context.actor.subject,)):
            raise TaskError(self._code("scope_invalid"), "select one resolved private person")
        current = require(self.app.tasks.identity.resolve_authenticated(
            context.actor.principal, observed_at=now))
        binding = require(self.app.tasks.identity.resolve_role(context.scope, observed_at=now))
        if (current.subject != context.actor.subject
                or current.binding_id != context.actor.binding_id
                or current.binding_revision != context.actor.binding_revision
                or binding != context.role_binding):
            raise TaskError(self._code("target_changed"),
                            "the selected person or role binding changed")
        target = self._target()
        require(self.policy.scope_access(
            operator, scope=target.authorization_scope, action=action,
            observed_at=now, owner=context.actor.subject, core_only=True))
        return target

    def _field_contract(self):
        contract = super()._field_contract()
        for name, maximum in RELATIONSHIP_PROFILE_FIELD_LIMITS.items():
            contract[name]["maximum_chars"] = maximum
        return contract


class DiaryAndRestManagement(GroupEditor):
    """Diary and dream writing preferences, plus the rest-reply gate.

    This group mixes two scopes: the five sleep fields are role-shared while the
    rest are window settings. The private window remains the editor target because
    enabling diary work synchronizes that participant's grants.

    Named ``DiaryAndRestManagement`` rather than ``RoleLifeManagement`` because
    ``role_work.life.management`` already exports a ``RoleLifeManagement`` that
    four other modules use as an access checker; two classes with one name in a
    single call site is how the authorization one gets used as an editor.
    """

    group, owner = LIFE_GROUP, LIFE_OWNER
    layers = life_fields()[0].layers
    target_layer = ConfigLayer.WINDOW
    error_prefix = "role_life"

    def _field_contract(self):
        contract = super()._field_contract()
        for name, definition in contract.items():
            definition["write_layer"] = (ConfigLayer.ROLE.value if name in SLEEP_FIELDS
                                          else ConfigLayer.WINDOW.value)
            definition["editable_layers"] = ([ConfigLayer.ROLE.value] if name in SLEEP_FIELDS
                                              else [ConfigLayer.WINDOW.value])
        return contract

    def _after_configure(self, operator, data, result):
        if "enabled" not in data["changes"]:
            return
        from companion_v2.role_work.life.management import RoleLifeManagement
        RoleLifeManagement(self.app, self.context).sync_configuration_grant(
            operator, data["changes"]["enabled"], result)


class GroupParticipationManagement(GroupEditor):
    """How the role behaves inside a group window: observation, interjection, repeat-follow.

    The group carries three independent chains and the page says so with three
    sections. Two of them are budgeted self-initiated speech -- interjection and
    repeat-follow -- and neither creates a send path: a group turn still starts
    from an admitted member message. Switches are listed before the limits they
    govern, because a closed switch makes the numbers below it inert.

    ``group_repeat_interrupt_text`` is the one field whose bound is not in the
    registry: ``participation.py`` also rejects control characters and requires a
    non-blank line, and ``contract_of`` publishes no string lengths. Its owner
    therefore re-runs the same validation on preview and on write, so an invalid
    line is refused at the server rather than silently stored.
    """

    group, owner = PARTICIPATION_GROUP, PARTICIPATION_OWNER
    layers = participation_fields()[0].layers
    error_prefix = "group_participation"


class WardrobeManagement(GroupEditor):
    """The role's wardrobe: whether it may be used at all, and how much reaches a turn.

    Wardrobe items are registered rows rather than settings, so the editor publishes
    only the switches and the two ceilings: how many items one prompt may name and
    how many pictures one outfit request may take in. Its layer tuple is role-only,
    which keeps a single conversation from narrowing what the whole role wears.

    The two prose fields carry a registered 800-character maximum, but the
    workbench draws TEXT as a plain textarea and its ``valid()`` only checks the
    type, so an over-long note is refused on write
    (``wardrobe_configuration_invalid``) instead of being silently truncated.
    """

    group, owner = WARDROBE_GROUP, WARDROBE_OWNER
    layers = wardrobe_fields()[0].layers
    error_prefix = "wardrobe"


class ReferenceManagement(GroupEditor):
    """Reference material for one role, and the daily-outfit path it feeds.

    The group carries no picture path: reference material is a registered asset, and
    a path in configuration text would bypass the ownership and visibility checks
    that asset layer exists to enforce. The two switches stay independent because
    agreeing to reference material is not agreeing to a picture every day, so the
    refusal codes differ: the validator's own ``photo_reference_configuration_invalid``
    and this editor's ``reference_*`` scope and target refusals.
    """

    group, owner = REFERENCE_GROUP, REFERENCE_OWNER
    layers = reference_fields()[0].layers
    error_prefix = "reference"


class ProactiveContactManagement(GroupEditor):
    """Global defaults, independent of any selected bot, role or participant.

    The runtime still checks each participant's own contact permission;
    saving a shared switch does not change any participant's authorization.
    """

    group, owner = CONTACT_GROUP, CONTACT_GROUP
    layers = contact_fields()[0].layers
    target_layer = ConfigLayer.INSTALLATION
    error_prefix = "proactive_contact"

    def _field_contract(self):
        """Publish daily quota overrides on windows, never on relationship scope."""
        contract = super()._field_contract()
        for name in WINDOW_QUOTA_FIELDS:
            entry = contract.get(name)
            if entry is not None:
                entry["editable_layers"] = [layer for layer in entry["editable_layers"]
                                            if layer != ConfigLayer.RELATIONSHIP.value]
        return contract
