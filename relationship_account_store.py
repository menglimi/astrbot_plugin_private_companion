try:  # package import
    from .relationship_account_store_shared import (
        ACCOUNT_MODES,
        ACCOUNT_ROLES,
        ADMIN_ACTORS,
        EVENT_ACTORS,
        GROUP_DIRECT_REASON,
        GROUP_ZERO_REASONS,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
        _weighted_integer,
    )
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import (
        ACCOUNT_MODES,
        ACCOUNT_ROLES,
        ADMIN_ACTORS,
        EVENT_ACTORS,
        GROUP_DIRECT_REASON,
        GROUP_ZERO_REASONS,
        PRIVATE_EVENT_REASONS,
        _canonical,
        _integer,
        _source_scope,
        _token,
        _weighted_integer,
    )
try:  # package import
    from .relationship_account_store_shared import ACCOUNT_MODES
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import ACCOUNT_MODES
try:  # package import
    from .relationship_account_store_part03 import RelationshipAccountStorePart03Mixin
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_part03 import RelationshipAccountStorePart03Mixin
try:  # package import
    from .relationship_account_store_part02 import RelationshipAccountStorePart02Mixin
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_part02 import RelationshipAccountStorePart02Mixin
try:  # package import
    from .relationship_account_store_part01 import RelationshipAccountStorePart01Mixin
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_part01 import RelationshipAccountStorePart01Mixin
try:  # package import
    from .relationship_account_store_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Any
try:  # package import
    from .relationship_account_store_shared import GroupAffinityAdmissionResult
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import GroupAffinityAdmissionResult
try:  # package import
    from .relationship_account_store_shared import OrderedDict
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import OrderedDict
try:  # package import
    from .relationship_account_store_shared import Path
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import Path
try:  # package import
    from .relationship_account_store_shared import RelationshipAccessDenied
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipAccessDenied
try:  # package import
    from .relationship_account_store_shared import RelationshipConflict
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipConflict
try:  # package import
    from .relationship_account_store_shared import RelationshipEventResult
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipEventResult
try:  # package import
    from .relationship_account_store_shared import RelationshipNotFound
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipNotFound
try:  # package import
    from .relationship_account_store_shared import RelationshipStoreError
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import RelationshipStoreError
try:  # package import
    from .relationship_account_store_shared import threading
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import threading
try:  # package import
    from .relationship_account_store_shared import time
except ImportError:  # direct test/import from the plugin directory
    from relationship_account_store_shared import time
class RelationshipAccountStore(RelationshipAccountStorePart01Mixin, RelationshipAccountStorePart02Mixin, RelationshipAccountStorePart03Mixin):
    """SQLite Shadow store with atomic settlement and redacted provenance."""

    def __init__(
        self, path: str | Path, *, active_migration_epoch: str, clock: Any = None,
        observability: Any = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._active_migration_epoch = _token(active_migration_epoch)
        if not self._active_migration_epoch:
            raise RelationshipStoreError("relationship_store_epoch_required")
        self._clock = clock if callable(clock) else time.time
        self._lock = threading.RLock()
        self._observability = observability
        self._account_cache: OrderedDict[str, tuple[int, dict[str, Any]]] = OrderedDict()
        self._account_cache_limit = 2048
        self._initialize()


__all__ = [
    "ACCOUNT_MODES", "ACCOUNT_ROLES", "GROUP_DIRECT_REASON", "PRIVATE_EVENT_REASONS",
    "GroupAffinityAdmissionResult",
    "RelationshipAccessDenied", "RelationshipAccountStore", "RelationshipConflict",
    "RelationshipEventResult", "RelationshipNotFound", "RelationshipStoreError",
]
