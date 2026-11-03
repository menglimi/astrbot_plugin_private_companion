"""Image plugin lifecycle assembly; the stable getter performs no I/O."""
from __future__ import annotations

from pathlib import Path

from .adapter import NativeImageProvider, PLUGIN_ID, SCHEMA
from .backends import NativeTransportBackend
from .output_configuration import settings as output_settings
from .output_routing import OutputRoutingBackend
from .owner import NativeImageOwner
from .package import package_hash
from .types import fingerprint


def build_provider(plugin, *, version: str):
    settings = plugin.config.get("native_v2", {})
    # This flag used to double as the output feature switch.  It must not gate
    # provider construction: the management API has to remain reachable so a
    # user can repair a disabled/migrated image configuration.  Output
    # availability is expressed by ``native_v2.output.enabled`` and enforced by
    # OutputRoutingBackend.operations/execute.
    if not plugin.enabled or not isinstance(settings, dict):
        return None
    directory = Path(plugin.data_dir) / "native_v2"
    backend = OutputRoutingBackend(output_settings(plugin), directory / "transport",
                                   context=getattr(plugin, "context", None))
    owner = NativeImageOwner(directory, backend, identity_key=fingerprint({"package": package_hash(), "version": version, "backend": backend.revision}))
    return NativeImageProvider(owner, package_version=version, wait_budget_seconds=backend.config.get("timeout_seconds", 300))


def native_status(plugin, *, version: str, display_name: str) -> dict:
    provider = getattr(plugin, "_native_v2_provider", None)
    ready = bool(plugin.enabled and provider is not None and not provider._closed)
    backend = provider.owner.backend.backend if provider is not None else ""
    return {"plugin_id": PLUGIN_ID, "plugin_name": display_name, "plugin_version": version,
            "status_schema_version": "image.status.v1", "api_version": SCHEMA, "installed": True,
            "enabled": plugin.enabled, "available": ready, "execution_mode": "native_v2",
            "managed_by_private_companion": False, "state": "native_ready" if ready else "unavailable",
            "reason": "" if ready else getattr(plugin, "_native_v2_error", "") or "native_provider_unavailable",
            "reuse_private_companion_settings": False, "reuse_private_companion_assets": False,
            "generation": {"available": ready, "api_version": SCHEMA}, "selected_backend": backend,
            "backends": {backend: {"configured": True}} if backend else {}}
