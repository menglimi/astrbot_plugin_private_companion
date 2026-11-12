"""V2-owned Image package settings, provider and endpoint management API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

from .bootstrap import build_provider
from .output_configuration import public_configuration, save_configuration, settings
from .output_routing import OnlineImageBackend
from .owner import NativeImageOwner
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
            # Windows 上配置文件偶尔会被同步盘、杀毒或编辑器短暂占用，
            # os.replace 会抛 PermissionError（WinError 32/5）。这里做几次
            # 很短的退避重试；仍失败才交给上层报“写不回数据目录”。
            last_error = None
            for attempt in range(5):
                try:
                    os.replace(temporary, self.path)
                    break
                except OSError as error:
                    # EACCES/EPERM，以及 Windows 的 5/32/33（拒绝访问、共享冲突、
                    # 文件被占用）。其它 OSError（磁盘满等）直接抛出。
                    if (error.errno not in {13, 26}
                            and getattr(error, "winerror", None) not in {5, 32, 33}):
                        raise
                    last_error = error
                    sleep(0.05 * (attempt + 1))
            else:
                raise NativeImageError("configuration_store_unavailable") from last_error
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
