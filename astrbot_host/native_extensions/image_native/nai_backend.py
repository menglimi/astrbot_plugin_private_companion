"""Bridge the installed NAI plugin into the bundled image owner's routes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from .owner import checked
from .types import BackendResult, NativeImageError, inspect_image

PLUGIN_NAME = "astrbot_plugin_nai_image"
_NAI_HTTP_STATUS_RE = re.compile(r"\bHTTP\s+(\d{3})\b|\bhttp_(\d{3})\b", re.IGNORECASE)
_NAI_SECRET_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{6,}|(?:sk|pk|pst|key|token)[_-][A-Za-z0-9._-]{8,}|https?://\S+)",
    re.IGNORECASE,
)
_NAI_NOTE_PREFIXES = ("🚫", "❌", "⏱", "📭", "🖼", "💥", "🔥", "⚠️", "❓", "NAI ", "提示词为空", "图片已生成但保存失败")


class NAIImageBackend:
    def __init__(self, context, directory):
        self.context = context
        self.directory = Path(directory)
        self.config = {"id": "nai"}

    def _metadata(self):
        getter = getattr(self.context, "get_registered_star", None)
        return getter(PLUGIN_NAME) if callable(getter) else None

    def _api(self):
        metadata = self._metadata()
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
                  "artist_slot_name": "", "reference_mode": "", "operations": []}
        try:
            metadata = self._metadata()
            if metadata is None:
                return result
            result["installed"] = True
            if getattr(metadata, "activated", True) is not True:
                result["reason"] = "nai_plugin_disabled"
                return result
            api = self._api()
            if api is None:
                result["reason"] = "nai_plugin_incompatible"
                return result
            status = api.status()
            capability = api.capability_status(self)
            available = capability.get("available") is True
            call_mode = str(status.get("call_mode", "direct"))
            declared = capability.get("operations", status.get("operations"))
            if isinstance(declared, (list, tuple)):
                operations = [operation for operation in ("generate", "edit")
                              if available and operation in declared]
            else:
                # Older NAI APIs do not declare operations. Both transports can
                # carry references; model-specific validation remains in NAI.
                operations = ["generate"] if available else []
                if (available and call_mode in ("openai", "novelai")
                        and status.get("reference_supported") is True):
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
                          operations=operations)
        except Exception:
            result.update(available=False, reason="nai_status_unavailable", operations=[])
        return result

    @property
    def operations(self):
        return tuple(self.status()["operations"])

    async def execute(self, invocation, references, before_dispatch):
        if invocation.model_call is not None:
            return await invocation.model_call("nai", invocation.operation,
                lambda dispatch: self._execute(invocation, references, dispatch), before_dispatch)
        return await self._execute(invocation, references, before_dispatch)

    async def _execute(self, invocation, references, before_dispatch):
        api = self._api()
        if api is None or invocation.operation not in self.operations:
            raise NativeImageError("nai_plugin_unavailable", unconfirmed=False)
        if invocation.parameters.keys() - {"width", "height", "denoising_strength"}:
            raise NativeImageError("unsupported_image_parameter", unconfirmed=False)
        prepared = invocation.envelope.get("nai_prompt") or {}
        prompt = prepared.get("positive", invocation.prompt)
        negative = prepared.get("negative", invocation.negative_prompt)
        roles = [reference.role for reference in references]
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
            try:
                result = await api.generate_for_companion(self, request)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                raise NativeImageError("nai_generation_timed_out", diagnostics=self._diagnostic(
                    "nai_generation_timed_out")) from None
            except Exception:
                raise NativeImageError("nai_generation_failed", diagnostics=self._diagnostic(
                    "nai_generation_failed")) from None
        if isinstance(result, dict) and result.get("handled") is False and result.get("reason") == "disabled":
            raise NativeImageError("nai_plugin_unavailable", unconfirmed=False,
                                   diagnostics=self._diagnostic("nai_plugin_unavailable", confirmed=True))
        if not isinstance(result, dict) or result.get("handled") is not True or not result.get("image_path"):
            code, status, detail, confirmed = self._classify_failure(result)
            raise NativeImageError(
                code,
                unconfirmed=not confirmed,
                diagnostics=self._diagnostic(code, confirmed=confirmed, status=status, detail=detail),
            )
        path = Path(result["image_path"])
        if not path.is_file() or not 0 < path.stat().st_size <= 50 * 1024 * 1024:
            raise NativeImageError("nai_output_unavailable", diagnostics=self._diagnostic("nai_output_unavailable"))
        image = await asyncio.to_thread(path.read_bytes)
        inspect_image(image)
        return BackendResult(images=(image,), receipt={"backend": "nai", "plugin": PLUGIN_NAME,
                                                       "response_received": True})

    @staticmethod
    def _sanitize_note(note):
        if not isinstance(note, str):
            return ""
        cleaned = _NAI_SECRET_RE.sub("[redacted]", note)
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned[:200]

    @classmethod
    def _classify_failure(cls, result):
        """Map structured NAI refusal notes while keeping opaque plugin text sealed."""
        if not isinstance(result, dict):
            return "nai_generation_unconfirmed", 0, "", False
        raw_reason = str(result.get("reason") or "").strip()
        raw_note = str(result.get("note") or "").strip()
        recognized = bool(raw_reason) or raw_note.startswith(_NAI_NOTE_PREFIXES)
        if not recognized:
            return "nai_generation_unconfirmed", 0, "", False
        detail = cls._sanitize_note(raw_note)
        status_match = _NAI_HTTP_STATUS_RE.search(f"{raw_reason} {raw_note}")
        status = int(status_match.group(1) or status_match.group(2)) if status_match else 0

        if raw_reason in {"no_token", "no_session", "openai_not_configured"} or (
            raw_note.startswith("❌") and any(k in raw_note for k in ("未配置", "未初始化"))
        ):
            return "nai_plugin_unavailable", 0, detail, True

        if raw_reason == "timeout" or raw_note.startswith("⏱"):
            return "nai_generation_timed_out", status, detail, False

        # Definitive pre-billing rejections: empty prompt, missing reference,
        # unsupported reference/director mode, or confirmed HTTP 400/422 refusal.
        if (
            raw_reason == "no_reference_image"
            or raw_reason.startswith(("reference_model_unsupported", "director_ref_rejected", "http_400", "http_422"))
            or "提示词为空，未发起生图" in raw_note
            or "需要输入一张源图片" in raw_note
            or "精准参考（director）被上游拒绝" in raw_note
            or ("参考" in raw_note and "不支持" in raw_note and raw_note.startswith("🚫"))
            or (status in (400, 422) and raw_note.startswith("🚫"))
        ):
            return "nai_request_rejected", status or 400, detail, True

        return "nai_generation_unconfirmed", status, detail, False

    @staticmethod
    def _diagnostic(code, *, confirmed=False, status=0, detail=""):
        return ({"endpoint_id": "nai", "status": status, "reason": code, "detail": detail,
                 "scope": "endpoint" if confirmed else "unconfirmed", "retry_after": ""},)
