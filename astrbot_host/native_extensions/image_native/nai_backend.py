"""Bridge the installed NAI plugin into the bundled image owner's routes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from .owner import checked
from .types import BackendResult, NativeImageError, inspect_image

PLUGIN_NAME = "astrbot_plugin_nai_image"
PROMPT_ERRORS = {
    "nai_prompt_format_invalid": "NAI 使用英文标签模式，请依据本轮人物、动作和场景改写为英文 NAI 标签后重试；"
                                 "角色本人出镜时保留人设里的发型发色、瞳色和种族等外貌特征。本次尚未提交生图。",
    "nai_identity_reference_mode": "NAI 的氛围参考不能固定人物身份，请先在 NAI 插件切换为精准参考或图生图。"
                                   "不要换用不带身份参考的生成来绕过本次一致性要求。",
    "nai_prompt_language_invalid": "NAI 当前使用英文自然语言模式，请根据本轮人物与场景改写为英文描述后重试。"
                                    "本次尚未提交生图请求。",
}


class NAIImageBackend:
    def __init__(self, context, directory):
        self.context = context
        self.directory = Path(directory)
        self.config = {"id": "nai"}

    def _api(self):
        getter = getattr(self.context, "get_registered_star", None)
        metadata = getter(PLUGIN_NAME) if callable(getter) else None
        if metadata is None or getattr(metadata, "activated", True) is not True:
            return None
        api = getattr(getattr(metadata, "star_cls", None), "extension_api", None)
        if not all(callable(getattr(api, name, None)) for name in
                   ("status", "capability_status", "generate_for_companion")):
            return None
        return api

    def status(self):
        """Return connection state and defaults without copying plugin credentials."""
        result = {"installed": False, "available": False, "reason": "nai_plugin_unavailable",
                  "call_mode": "", "api_profile": "", "default_model": "",
                  "default_style": "", "default_size": "", "prompt_format": "",
                  "artist_slot_name": "", "reference_mode": "", "prompt_contract_version": 0, "operations": []}
        try:
            api = self._api()
            if api is None:
                return result
            result["installed"] = True
            status = api.status()
            capability = api.capability_status(self)
            available = capability.get("available") is True
            call_mode = str(status.get("call_mode", "direct"))
            operations = ["generate"] if available else []
            if available and call_mode == "openai" and status.get("reference_supported") is True:
                operations.append("edit")
            result.update(available=available, reason=str(capability.get("reason", ""))[:80],
                          call_mode=call_mode,
                          api_profile=str(status.get("api_profile", ""))[:40],
                          default_model=str(status.get("default_model", ""))[:160],
                          default_style=str(status.get("default_style", ""))[:80],
                          default_size=str(status.get("default_size", ""))[:80],
                          prompt_format=("nai" if "nai" in str(status.get("prompt_format", "")).casefold()
                                         else "natural" if status.get("prompt_format") else ""),
                          artist_slot_name=str(status.get("artist_slot_name", ""))[:80],
                          reference_mode=str(status.get("reference_mode", ""))[:40],
                          prompt_contract_version=(1 if status.get("companion_prompt_contract_version") == 1 else 0),
                          operations=operations)
        except Exception:
            result.update(available=False, reason="nai_status_unavailable", operations=[])
        return result

    @property
    def operations(self):
        return tuple(self.status()["operations"])

    def validate_prompt(self, prompt, negative="", roles=()):
        """Reject known prompt and identity mismatches before image dispatch.

        Args:
            prompt: Content prepared for the selected NAI mode.
            negative: Explicit exclusions from the image request.
            roles: The intended roles of any selected references.

        Raises:
            NativeImageError: The installed plugin rejects the local contract.
        """
        validator = getattr(self._api(), "validate_companion_prompt", None)
        if callable(validator):
            result = validator({"prompt_text": prompt, "negative": negative, "reference_roles": list(roles)})
            if result.get("valid") is False:
                code = result.get("reason")
                raise NativeImageError(code if code in PROMPT_ERRORS else "nai_prompt_format_invalid",
                                       unconfirmed=False)

    async def execute(self, invocation, references, before_dispatch):
        api = self._api()
        if api is None or invocation.operation not in self.operations:
            raise NativeImageError("nai_plugin_unavailable", unconfirmed=False)
        if invocation.parameters.keys() - {"width", "height", "denoising_strength"}:
            raise NativeImageError("unsupported_image_parameter", unconfirmed=False)
        prepared = invocation.envelope.get("nai_prompt") or {}
        prompt = prepared.get("positive", invocation.prompt)
        negative = prepared.get("negative", invocation.negative_prompt)
        roles = [reference.role for reference in references]
        self.validate_prompt(prompt, negative, roles)
        request = {"prompt_text": prompt, "negative": negative,
                   "workflow_kind": invocation.operation,
                   "session_key": invocation.scope_key, "reference_roles": roles,
                   "character_source": "companion"}
        parameters = invocation.parameters
        if "width" in parameters or "height" in parameters:
            if "width" not in parameters or "height" not in parameters:
                raise NativeImageError("invalid_image_parameter", unconfirmed=False)
            request["size"] = f"{parameters['width']}x{parameters['height']}"
        if "denoising_strength" in parameters:
            if not 0 < parameters["denoising_strength"] <= 1:
                raise NativeImageError("invalid_image_parameter", unconfirmed=False)
            request["strength"] = parameters["denoising_strength"]
        self.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="nai-", dir=self.directory) as temporary:
            paths = []
            for index, reference in enumerate(references):
                info = inspect_image(reference.data)
                path = Path(temporary) / f"reference-{index}.{info['media_type'].split('/')[1]}"
                await asyncio.to_thread(path.write_bytes, reference.data)
                paths.append(str(path))
            if paths:
                request["reference_image_paths"] = paths
            if api is not self._api() or invocation.operation not in self.operations:
                raise NativeImageError("nai_plugin_unavailable", unconfirmed=False)
            # The existing owner fence must run before NAI can incur any charge.
            await checked(before_dispatch)
            result = await api.generate_for_companion(self, request)
        if not isinstance(result, dict) or result.get("handled") is not True or not result.get("image_path"):
            raise NativeImageError("nai_generation_unconfirmed")
        path = Path(result["image_path"])
        if not path.is_file() or not 0 < path.stat().st_size <= 50 * 1024 * 1024:
            raise NativeImageError("nai_output_unavailable")
        image = await asyncio.to_thread(path.read_bytes)
        inspect_image(image)
        return BackendResult(images=(image,), receipt={"backend": "nai", "plugin": PLUGIN_NAME,
                                                       "response_received": True})
