try:  # package import
    from .unified_person_registry_shared import (
        PERSON_PURGE_RETENTION_SECONDS,
        _CONTROL_CHARACTER_RE,
        _FORBIDDEN,
        _IDENTITY_ASSURANCE_RANK,
        _IDENTITY_FIELDS,
        _KEY_SEPARATOR_RE,
        _LOCK,
        _P4_EFFECT_ALLOWED_FIELDS,
        _P4_EFFECT_FORBIDDEN_FIELDS,
        _P4_EFFECT_TIMESTAMP_RE,
        _P4_EFFECT_TOKEN_RE,
        _P4_EFFECT_VERSION,
        _PROFILE_FACT_FIELDS,
        _contains_exact_value,
        _contains_forbidden_key,
        _fingerprint,
        _identity,
        _normalize_p4_effect_event,
        _now,
        _operation_id,
        _p4_effect_container,
        _p4_effect_fingerprint,
        _p4_effect_state,
        _p4_effect_summary,
        _person_identity_assurance,
        _replay_p4_effect_entry,
        _root,
        _safe,
        _safe_affinity_score,
        _text,
        _timestamp,
    )
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import (
        PERSON_PURGE_RETENTION_SECONDS,
        _CONTROL_CHARACTER_RE,
        _FORBIDDEN,
        _IDENTITY_ASSURANCE_RANK,
        _IDENTITY_FIELDS,
        _KEY_SEPARATOR_RE,
        _LOCK,
        _P4_EFFECT_ALLOWED_FIELDS,
        _P4_EFFECT_FORBIDDEN_FIELDS,
        _P4_EFFECT_TIMESTAMP_RE,
        _P4_EFFECT_TOKEN_RE,
        _P4_EFFECT_VERSION,
        _PROFILE_FACT_FIELDS,
        _contains_exact_value,
        _contains_forbidden_key,
        _fingerprint,
        _identity,
        _normalize_p4_effect_event,
        _now,
        _operation_id,
        _p4_effect_container,
        _p4_effect_fingerprint,
        _p4_effect_state,
        _p4_effect_summary,
        _person_identity_assurance,
        _replay_p4_effect_entry,
        _root,
        _safe,
        _safe_affinity_score,
        _text,
        _timestamp,
    )
try:  # package import
    from .unified_person_registry_shared import PERSON_PURGE_RETENTION_SECONDS
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import PERSON_PURGE_RETENTION_SECONDS
try:  # package import
    from .unified_person_registry_purge import UnifiedPersonRegistryPurgeMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_purge import UnifiedPersonRegistryPurgeMixin
try:  # package import
    from .unified_person_registry_projection_p4_overlay import UnifiedPersonRegistryProjectionP4OverlayMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_projection_p4_overlay import UnifiedPersonRegistryProjectionP4OverlayMixin
try:  # package import
    from .unified_person_registry_link_unlink import UnifiedPersonRegistryLinkUnlinkMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_link_unlink import UnifiedPersonRegistryLinkUnlinkMixin
try:  # package import
    from .unified_person_registry_create_update_facts import UnifiedPersonRegistryCreateUpdateFactsMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_create_update_facts import UnifiedPersonRegistryCreateUpdateFactsMixin
try:  # package import
    from .unified_person_registry_core_namespace import UnifiedPersonRegistryCoreNamespaceMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_core_namespace import UnifiedPersonRegistryCoreNamespaceMixin
try:  # package import
    from .unified_person_registry_archive import UnifiedPersonRegistryArchiveMixin
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_archive import UnifiedPersonRegistryArchiveMixin
try:  # package import
    from .unified_person_registry_shared import Any
except ImportError:  # direct test/import from the plugin directory
    from unified_person_registry_shared import Any
class UnifiedPersonRegistry(UnifiedPersonRegistryArchiveMixin, UnifiedPersonRegistryCoreNamespaceMixin, UnifiedPersonRegistryCreateUpdateFactsMixin, UnifiedPersonRegistryLinkUnlinkMixin, UnifiedPersonRegistryProjectionP4OverlayMixin, UnifiedPersonRegistryPurgeMixin):
    """The only chat-side writer for Unified Person identity state."""

    def __init__(self, store: dict[str, Any]) -> None:
        if not isinstance(store, dict):
            raise ValueError("store_invalid")
        self._store = store


__all__ = ["UnifiedPersonRegistry"]
