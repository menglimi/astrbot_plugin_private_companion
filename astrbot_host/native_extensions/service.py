"""Host helpers for internal services, without independent plugin registration."""

from __future__ import annotations

import json


class NativeService:
    async def get_kv_data(self, key, default=None):
        try:
            values = json.loads((self.data_dir / "host-state.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            values = {}
        return values.get(key, default)

    async def put_kv_data(self, key, value):
        path = self.data_dir / "host-state.json"
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            values = {}
        values[key] = value
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
        temporary.replace(path)

    def v2_api(self):
        from ..integration_registry import UnifiedCompanionExtensionAPI
        if not hasattr(self, "_v2_api"):
            self._v2_api = UnifiedCompanionExtensionAPI(self.controller, claim=False)
        return self._v2_api

    def v2_host_api(self):
        """Return the V2-owned host facade for this native extension.

        Native modules are assembled by the V2 controller.  Keeping this
        lookup on the shared service base makes that ownership explicit and
        avoids the old private-companion plugin bridge terminology.
        """
        return self.v2_api()

    def native_extension(self, extension_id):
        """Resolve another bundled V2 module through the active registry."""
        registry = getattr(self.controller, "native_extensions", None)
        getter = getattr(registry, "active_service", None)
        return getter(extension_id) if callable(getter) else None
