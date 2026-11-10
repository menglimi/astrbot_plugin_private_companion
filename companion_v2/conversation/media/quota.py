"""Daily per-scope image generation limits, counted from confirmed results.

Image operations are counted per requester for the local day of the
conversation timezone. Failed and unknown results do not consume an allowance;
only a confirmed successful image operation does.
"""

from datetime import timezone
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.configuration.owners import SparseConfigurationOwner
from companion_v2.configuration.registry import FieldDefinition, ValueKind
from companion_v2.conversation.perception_settings import (GROUP as PERCEPTION_GROUP,
    PerceptionConfigurationOwner, configuration_fields as perception_fields,
    target_for as perception_target_for)
from companion_v2.execution.models import TaskError, stamp
from companion_v2.foundation.refs import ScopeKind

# Kept as the stable persisted parent-task kind; importing it from proactive
# would make the conversation package depend on the proactive package.
PROACTIVE_COMPOSE_KIND = "proactive.compose"

logger = logging.getLogger(__name__)

GROUP = "conversation.media_quota"
OWNER = "conversation.media_quota"
PERCEPTION_TIMEZONE_FIELD = PERCEPTION_GROUP + ".calendar_timezone"
# The perception owner validates the whole group on write, so reads must ask for
# the whole group even though only the timezone is used here.
PERCEPTION_TIMEZONE_FIELDS = perception_fields()

SCOPE_LABELS = {"private_owner": "primary user private chat", "private_friend": "other private chats",
                "group": "group chats", "proactive": "proactive messages"}
SCOPE_FIELDS = ("private_owner_max_daily", "private_friend_max_daily", "group_max_daily", "proactive_max_daily")
QUOTA_FIELDS = tuple(
    FieldDefinition(GROUP + "." + name, GROUP, GROUP, ValueKind.INTEGER, -1,
                    layers=(ConfigLayer.INSTALLATION, ConfigLayer.ROLE), owner_id=OWNER,
                    minimum=-1, maximum=100)
    for name in SCOPE_FIELDS
)


def configuration_fields():
    return QUOTA_FIELDS


class ImageQuotaConfigurationOwner(SparseConfigurationOwner):
    owner_id = OWNER


class ImageQuotaGate:
    def __init__(self, messages):
        self.runtime = messages.runtime
        self.timezone_name = messages.timezone_name
        self.owner = ImageQuotaConfigurationOwner(self.runtime)

    def admit(self, task, at):
        """Raise when this parent task may not dispatch another image action today."""
        context = task.spec.context
        scope = self._classify(task)
        self._admit(context, scope, at)

    def admit_native(self, context, at):
        """Apply the configured limits to a new core tool call without a reply task."""
        self._admit(context, self._classify_context(context), at)

    def _admit(self, context, scope, at):
        from .output_settings import effective_settings, image_request_denial
        settings = effective_settings(self.runtime, context)
        if scope != "proactive":
            denial = image_request_denial(self.runtime, context, settings)
            if denial is not None:
                # This is the only place where a denial means "someone actually
                # tried to generate or edit an image and was refused", so it is
                # the one worth surfacing. Turn-level probes stay at DEBUG.
                logger.info("[CompanionV2] 生图请求被拒 code=%s scope=%s subject=%s group=%s",
                            denial[0], scope, context.actor.subject.subject_id,
                            context.scope.native_scope_id)
                raise TaskError(*denial)
        limit = self._limit(context, scope)
        if limit < 0:
            return
        if self._used(context, scope, at) >= limit:
            if limit == 0:
                raise TaskError("image_quota_closed",
                                f"image generation for {SCOPE_LABELS[scope]} is closed by a zero daily limit")
            raise TaskError("image_quota_exhausted",
                            f"the daily image generation allowance for {SCOPE_LABELS[scope]} is exhausted; "
                            "raise the limit or set it to -1 to remove the cap")

    def _classify(self, task):
        if task.spec.kind == PROACTIVE_COMPOSE_KIND:
            return "proactive"
        return self._classify_context(task.spec.context)

    def _classify_context(self, context):
        if context.scope.kind is ScopeKind.GROUP:
            return "group"
        row = self.runtime.read_one("SELECT tier FROM subject_access_tiers WHERE subject_kind = ? AND subject_id = ?",
                                    (context.actor.subject.kind.value, context.actor.subject.subject_id))
        return "private_owner" if row is not None and row["tier"] == "core" else "private_friend"

    def _limit(self, context, scope):
        target = ConfigTarget(context.scope.installation_id, ConfigLayer.WINDOW, context.role_binding.role,
                              context.scope, subject=context.actor.subject)
        snapshot = self.owner.read(target, QUOTA_FIELDS)
        return snapshot.fields[GROUP + "." + scope + "_max_daily"].value

    def _used(self, context, scope, at):
        # The cutoff must be a stamp() string: binding a datetime lets sqlite3
        # render it as "2026-09-20 16:00:00", and a space sorts before the "T" in
        # every stored timestamp, so the comparison would silently count attempts
        # from before the local day.
        cutoff = stamp(self._day_start(at, context))
        predicates = [
                      "json_extract(p.record_json, '$.spec.context.role_binding.scope.installation_id') = ?",
                      "json_extract(p.record_json, '$.spec.context.role_binding.scope.bot_id') = ?",
                      "json_extract(p.record_json, '$.spec.context.role_binding.role.subject_id') = ?",
                      "json_extract(p.record_json, '$.spec.context.actor.subject.subject_id') = ?"]
        parameters = [cutoff, context.scope.installation_id, context.scope.bot_id,
                      context.role_binding.role.subject_id, context.actor.subject.subject_id]
        if scope == "proactive":
            predicates.append("p.kind = 'proactive.compose'")
        else:
            predicates.append("p.kind != 'proactive.compose'")
        predicates.append("json_extract(p.record_json, '$.spec.context.audience.scope.kind') = ?")
        parameters.append("group" if scope == "group" else context.scope.kind.value)
        join = ""
        if scope in ("private_owner", "private_friend"):
            join = ("""LEFT JOIN subject_access_tiers s
                    ON s.subject_kind = json_extract(p.record_json, '$.spec.context.actor.subject.kind')
                    AND s.subject_id = json_extract(p.record_json, '$.spec.context.actor.subject.subject_id')""")
            predicates.append("COALESCE(s.tier, 'standard') " + ("= 'core'" if scope == "private_owner" else "!= 'core'"))
        row = self.runtime.read_one(f"""SELECT COUNT(*) FROM extension_tool_invocations i
            JOIN execution_tasks p ON p.task_id = i.parent_task_id {join}
            WHERE i.action IN ('image.generate', 'image.edit', 'image.mask_edit')
            AND i.state = 'succeeded' AND i.created_at >= ?
            AND {' AND '.join(predicates)}""", tuple(parameters))
        if scope == "proactive":
            return row[0]
        # Native operations have their own real child task. They share the role,
        # requester and local-day limits with legacy user image requests, but are
        # never counted as proactive composition or as a fabricated reply batch.
        from companion_v2.conversation.native_tool_results import PREFIX
        native = self.runtime.read_one(f"""SELECT COUNT(*) FROM runtime_metadata n
            JOIN execution_tasks p ON p.task_id = json_extract(n.value, '$.task_id') {join}
            WHERE substr(n.key, 1, ?) = ?
            AND json_extract(n.value, '$.action') IN ('image.generate', 'image.edit', 'image.mask_edit')
            AND json_extract(n.value, '$.state') = 'succeeded'
            AND p.created_at >= ? AND {' AND '.join(predicates)}""", (len(PREFIX), PREFIX, *parameters))
        return row[0] + native[0]

    def _day_start(self, at, context=None):
        """The local day boundary, resolved the same way the role's calendar is.

        Counting from a different zone than the calendar the role reasons with made
        the quota reset at a different midnight than the role's own day. The
        perception setting is the operator-visible one, so it wins; the
        conversation timezone stays as the fallback.
        """
        zone = None
        if context is not None:
            try:
                snapshot = PerceptionConfigurationOwner(self.runtime).read(
                    perception_target_for(context), PERCEPTION_TIMEZONE_FIELDS)
                candidate = snapshot.fields.get(PERCEPTION_TIMEZONE_FIELD)
                zone = ZoneInfo(candidate.value) if candidate is not None and candidate.value else None
            except (ZoneInfoNotFoundError, ValueError, KeyError, TaskError):
                zone = None
        if zone is None:
            try:
                zone = ZoneInfo(self.timezone_name)
            except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
                raise TaskError("image_quota_timezone",
                                "image quota requires a valid conversation timezone") from error
        return at.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
