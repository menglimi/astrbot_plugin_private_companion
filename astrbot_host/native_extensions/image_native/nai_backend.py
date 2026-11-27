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
_MAYBE_BILLED_MARKERS = ("可能计费", "可能已计费", "可能已扣费")
_SUBJECT_MODES = frozenset(("character", "scene_only", "edit_preserve"))


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
        if invocation.parameters.keys() - {"width", "height", "aspect_ratio", "denoising_strength"}:
            raise NativeImageError("unsupported_image_parameter", unconfirmed=False)
        prepared = invocation.envelope.get("nai_prompt") or {}
        prompt = prepared.get("positive", invocation.prompt)
        negative = prepared.get("negative", invocation.negative_prompt)
        roles = [reference.role for reference in references]
        image_intent = invocation.envelope.get("image_intent")
        if not isinstance(image_intent, dict):
            image_intent = {}
        arguments = invocation.envelope.get("arguments")
        raw_subject_mode = image_intent.get("subject_mode")
        if not raw_subject_mode and isinstance(arguments, dict):
            raw_subject_mode = arguments.get("subject_mode")
        if isinstance(raw_subject_mode, str) and raw_subject_mode in _SUBJECT_MODES:
            subject_mode = raw_subject_mode
        elif invocation.operation in {"edit", "mask_edit"} and "identity" not in roles and "character" not in roles:
            subject_mode = "edit_preserve"
        else:
            subject_mode = "character"
        preserve_source_style = image_intent.get("preserve_source_style")
        if type(preserve_source_style) is not bool:
            preserve_source_style = subject_mode == "edit_preserve"
        allow_fallback_references = image_intent.get("allow_fallback_references")
        if type(allow_fallback_references) is not bool:
            allow_fallback_references = subject_mode == "character"
        request = {"prompt_text": prompt, "negative": negative,
                   "workflow_kind": invocation.operation,
                   "session_key": invocation.scope_key, "reference_roles": roles,
                   "character_source": "companion",
                   "subject_mode": subject_mode,
                   "preserve_source_style": preserve_source_style,
                   "allow_fallback_references": allow_fallback_references}
        parameters = invocation.parameters
        if "width" in parameters or "height" in parameters:
            if "width" not in parameters or "height" not in parameters:
                raise NativeImageError("invalid_image_parameter", unconfirmed=False)
            request["size"] = f"{parameters['width']}x{parameters['height']}"
        elif "aspect_ratio" in parameters:
            request["ratio"] = parameters["aspect_ratio"]
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
            code, status, detail, confirmed, retryable = self._classify_failure(result)
            raise NativeImageError(
                code,
                unconfirmed=not confirmed,
                diagnostics=self._diagnostic(
                    code, confirmed=confirmed, status=status, detail=detail, retryable=retryable,
                ),
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
        """Map structured NAI failure fields and recognized notes while keeping opaque text sealed."""
        if not isinstance(result, dict):
            return "nai_generation_unconfirmed", 0, "", False, None
        raw_reason = str(result.get("reason") or "").strip()
        raw_note = str(result.get("note") or "").strip()
        raw_status = result.get("http_status")
        explicit_status = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else 0
        raw_retryable = result.get("retryable")
        explicit_retryable = raw_retryable if type(raw_retryable) is bool else None

        recognized = (
            bool(raw_reason)
            or explicit_status > 0
            or explicit_retryable is not None
            or raw_note.startswith(_NAI_NOTE_PREFIXES)
        )
        if not recognized:
            return "nai_generation_unconfirmed", 0, "", False, None
        detail = cls._sanitize_note(raw_note)
        status_match = _NAI_HTTP_STATUS_RE.search(f"{raw_reason} {raw_note}")
        status = explicit_status or (
            int(status_match.group(1) or status_match.group(2)) if status_match else 0
        )

        if (
            raw_reason in {"no_token", "no_session", "openai_not_configured", "openai_key_missing"}
            or (raw_note.startswith("❌") and any(k in raw_note for k in ("未配置", "未初始化")))
            or explicit_status in (401, 403)
            or raw_reason.startswith(("http_401", "http_403"))
        ):
            return "nai_plugin_unavailable", status, detail, True, False

        if raw_reason == "timeout" or raw_note.startswith("⏱"):
            return "nai_generation_timed_out", status, detail, False, explicit_retryable

        # Partial-billing guard (e.g., multi-reference Vibe encoding that may
        # already have billed earlier steps before a 429/503 failure): never
        # classify as confirmed-unbilled or retryable.
        if any(marker in raw_note for marker in _MAYBE_BILLED_MARKERS):
            return "nai_generation_unconfirmed", status, detail, False, False

        if explicit_retryable is not False and (
            status == 429 or raw_reason in {"rate_limited", "http_429"} or raw_reason.startswith("http_429")
        ):
            return "backend_rate_limited", 429, detail, True, True

        # Definitive pre-billing rejections: empty prompt, missing reference,
        # unsupported reference/director mode, or confirmed HTTP 400/422 refusal.
        if (
            raw_reason == "no_reference_image"
            or raw_reason.startswith(("reference_model_unsupported", "director_ref_rejected", "http_400", "http_422"))
            or "提示词为空，未发起生图" in raw_note
            or "需要输入一张源图片" in raw_note
            or "精准参考（director）被上游拒绝" in raw_note
            or ("参考" in raw_note and "不支持" in raw_note and raw_note.startswith("🚫"))
            or (status in (400, 422) and (raw_note.startswith("🚫") or explicit_status in (400, 422)))
        ):
            return "nai_request_rejected", status or 400, detail, True, False

        # Confirmed transient upstream errors (HTTP 502/503/504, gateway/network
        # failures, or explicit retryable=True non-timeout errors): NAI finished
        # a synchronous exchange with a definite failure rather than timing out,
        # so nothing was committed and the turn can end cleanly instead of
        # locking in an unconfirmed wait state.  Respect explicit retryable=False
        # (for example partially-billed failures) by leaving them unconfirmed.
        if explicit_retryable is not False and (
            status in (502, 503, 504)
            or raw_reason in {
                "upstream_unavailable", "bad_gateway", "service_unavailable",
                "gateway_timeout", "network_error", "connection_error",
                "http_502", "http_503", "http_504",
            }
            or raw_reason.startswith(("http_502", "http_503", "http_504"))
            or explicit_retryable is True
        ):
            return "nai_upstream_unavailable", status, detail, True, True

        return "nai_generation_unconfirmed", status, detail, False, explicit_retryable

    @staticmethod
    def _diagnostic(code, *, confirmed=False, status=0, detail="", retryable=None):
        entry = {"endpoint_id": "nai", "status": status, "reason": code, "detail": detail,
                 "scope": "endpoint" if confirmed else "unconfirmed", "retry_after": ""}
        if type(retryable) is bool:
            entry["retryable"] = retryable
        return (entry,)
