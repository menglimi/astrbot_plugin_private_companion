"""V2-owned Image package settings, provider and endpoint management API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .bootstrap import build_provider
from .output_configuration import public_configuration, save_configuration, settings
from .output_routing import OnlineImageBackend
from .owner import NativeImageOwner
from .nai_backend import NAIImageBackend
from .types import ImageInvocation, NativeImageError


class ImageConfig(dict):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError, ValueError) as error:
                raise NativeImageError("native_configuration_invalid") from error
            if type(saved) is not dict or type(saved.get("native_v2")) is not dict:
                raise NativeImageError("native_configuration_invalid")
            super().__init__(saved)
        else:
            super().__init__({"native_v2": {
                "enabled": True,
                "backend": "auto",
                "output": {"enabled": False, "backend": "auto", "endpoints": [],
                           "sd_webui": {}, "comfyui": {}},
            }})

    def save_config(self):
        temporary = self.path.with_name("." + self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(self, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True, separators=(",", ":")))
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class ImageExtensionAPI:
    def __init__(self, *, data_root: Path, config_path: Path, version: str, context=None):
        self.data_dir = Path(data_root).resolve() / "extensions" / "image"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config = ImageConfig(config_path)
        self.context = context
        self.enabled = True
        self._native_v2_provider = build_provider(self, version=version)
        if self._native_v2_provider is None:
            raise NativeImageError("image_provider_unavailable")

    def companion_v2_native(self):
        return self._native_v2_provider

    def output_configuration(self):
        return public_configuration(self)

    def save_output_configuration(self, revision: str, changes: dict):
        return save_configuration(self, revision, changes)

    def validate_output_prompt(self, request):
        """Preflight the first usable route without performing generation.

        Args:
            request: The host-owned request with prepared prompt variants.

        Returns:
            A known NAI error code, or an empty string when no mismatch is found.
        """
        provider = self.companion_v2_native()
        operation = request["action"].split(".", 1)[1]
        routes = getattr(provider.owner.backend, "_routes", None)
        if not callable(routes):
            return ""
        for backend in routes(operation):
            if operation not in backend.operations:
                continue
            if isinstance(backend, NAIImageBackend):
                prepared = request.get("nai_prompt") or request.get("image_prompt") or {}
                args = request["arguments"]
                try:
                    backend.validate_prompt(prepared.get("positive", args["prompt"]),
                                            prepared.get("negative", args.get("negative_prompt", "")),
                                            args.get("reference_roles", ()))
                except NativeImageError as error:
                    return error.code
            break
        return ""
    async def test_output_endpoint(self, endpoint_id: str, operation_key: str, *, before_dispatch):
        source = settings(self)
        endpoint = next((item for item in source["endpoints"] if item["id"] == endpoint_id), None)
        if endpoint is None or not endpoint["enabled"]:
            raise NativeImageError("native_endpoint_unconfigured")
        backend = OnlineImageBackend(endpoint, source.get("network", {}))
        owner = NativeImageOwner(self.data_dir / "endpoint_tests", backend,
                                 identity_key="v2-image-output-endpoint-tests-v1")
        started = monotonic()
        try:
            result = await owner.execute(ImageInvocation(
                "dashboard-endpoint-test", operation_key, operation_key, operation_key,
                "generate", "A simple blue cup on a plain white table.",
                envelope={"endpoint_id": endpoint_id}), before_dispatch=before_dispatch)
            return {"status": result["state"], "operation_id": result["id"],
                    "elapsed_ms": round((monotonic() - started) * 1000),
                    "error": result.get("error", ""),
                    # Redacted, bounded per-endpoint evidence for the dashboard.
                    # The model-facing receipt contract is untouched.
                    "diagnostics": list(result.get("receipt", {}).get("diagnostics", ()) or ())}
        finally:
            await owner.close()

    def close(self):
        provider = self._native_v2_provider
        if provider is None:
            return
        provider._closed = True
        provider.owner._closed = True
        provider.owner.backend._closed = True
