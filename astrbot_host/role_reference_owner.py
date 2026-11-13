"""Reuse an explicitly mapped role's existing QQ image material for Native."""

from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.references.ownership import ReferenceOwnerCatalog


QQ_PLATFORMS = frozenset(('aiocqhttp', 'qq_official', 'qq_official_webhook'))


class RoleReferenceOwnerResolver:
    def __init__(self, runtime, bridge):
        self.runtime = runtime
        self.bridge = bridge
        self.materials = ReferenceOwnerCatalog(runtime)

    def __call__(self, context):
        original = RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                                context.role_binding.role.subject_id)
        if self.bridge.platform_name(context.scope) != 'wechat_native':
            return original
        # Consult live host mappings on every read, including media fetch/recheck.
        settings = self.bridge.settings
        current = {m.persona_id for m in settings.personas
                   if m.bot_id == original.bot_id and m.role_id == original.persona_id}
        if len(current) != 1 or next(iter(current)) in ('', '[%None]'):
            return original
        persona = next(iter(current))
        mapped = {m.bot_id for m in settings.personas
                  if m.persona_id == persona and m.role_id == original.persona_id}
        candidates = {RoleOwnerRef(original.installation_id, a.bot_id, original.persona_id)
                      for a in settings.accounts if a.bot_id in mapped
                      and a.bot_id != original.bot_id
                      and self.bridge.platform_name(a) in QQ_PLATFORMS}
        if not candidates:
            return original
        with self.runtime.read_snapshot():
            if self.materials.populated(original, context.role_binding.role):
                raise TaskError('reference_owner_conflict',
                                'Native already owns reference material; review it before sharing')
            populated = {owner for owner in candidates if self.materials.populated(owner, context.role_binding.role)}
        selected = populated or candidates
        if len(selected) != 1:
            raise TaskError('reference_owner_ambiguous',
                            'the mapped role has multiple QQ material owners')
        return next(iter(selected))
