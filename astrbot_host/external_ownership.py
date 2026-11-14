"""Host-facing alias for the shared external ownership ledger."""

from companion_v2.external_ownership import (
    KINDS, SCHEDULE_API_VERSION, STATE_API_VERSION, ExternalOwnershipError,
    ExternalOwnershipRegistry, registry_for,
)

__all__ = ["ExternalOwnershipError", "ExternalOwnershipRegistry", "KINDS",
           "SCHEDULE_API_VERSION", "STATE_API_VERSION", "registry_for"]
