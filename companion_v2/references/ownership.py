"""Read-only discovery of existing role material through its owning services."""

from companion_v2.configuration.models import ConfigLayer, ConfigTarget
from companion_v2.execution.models import digest

from .configuration import ReferenceConfigurationOwner, configuration_fields as reference_fields
from .repositories import ReferenceAssetRepository
from .wardrobe import WardrobeConfigurationOwner, configuration_fields as wardrobe_fields


class ReferenceOwnerCatalog:
    def __init__(self, runtime):
        self.runtime = runtime
        self.assets = ReferenceAssetRepository(runtime)

    def populated(self, owner, role):
        key = digest(['role-references.v1', owner])
        with self.runtime.read_snapshot():
            # Withdrawn records also count: existing data must not be hidden.
            if (self.assets.list(key, state=None, limit=1)
                    or self.assets.list_items(key, state=None, limit=1)):
                return True
            target = ConfigTarget(owner.installation_id, ConfigLayer.ROLE,
                                  role, bot_id=owner.bot_id)
            for service, fields in ((ReferenceConfigurationOwner, reference_fields),
                                    (WardrobeConfigurationOwner, wardrobe_fields)):
                snapshot = service(self.runtime).read(target, fields())
                if any(item.source is not None for item in snapshot.fields.values()):
                    return True
        return False
