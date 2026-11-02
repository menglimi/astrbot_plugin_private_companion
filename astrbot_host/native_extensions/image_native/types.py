"""Internal image values; the public provider contract belongs to the V2 host."""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from PIL import Image, ImageChops


class NativeImageError(ValueError):
    """A failed image attempt plus what is safe to do with it.

    ``unconfirmed`` is the retry-safety flag.  It defaults to ``True`` and only
    a caller holding *positive* evidence that the provider never executed the
    request (a definitive 4xx refusal, a local validation failure, an authority
    revocation) may pass ``False``.  The owner turns a confirmed refusal into
    ``failed`` (safe to retry, nothing was billed) and keeps everything else
    ``unknown`` (never retried, may already be billed).  Flipping the default
    would quietly turn a possibly-billed attempt into a retry, so the default
    stays conservative.

    ``diagnostics`` carries per-endpoint, already-redacted evidence:
    ``{"endpoint_id", "status", "reason", "detail"}``.  It is stored on the
    failed/unknown operation receipt for operators and the dashboard test API;
    it never enters the model-facing receipt contract.
    """

    def __init__(self, code: str, *, diagnostics=(), unconfirmed: bool = True):
        super().__init__(code)
        self.code = code
        self.diagnostics = tuple(dict(item) for item in diagnostics)
        self.unconfirmed = bool(unconfirmed)


def document(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise NativeImageError("invalid_image_request") from exc


def fingerprint(value: Any) -> str:
    return hashlib.sha256(document(value).encode("utf-8")).hexdigest()


def require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise NativeImageError("invalid_" + label)
    return value


def inspect_image(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 50 * 1024 * 1024:
        raise NativeImageError("image_size_limit")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            media_type = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
            if not media_type or image.width * image.height > 40_000_000:
                raise ValueError("unsupported image")
            width, height, frames = image.width, image.height, getattr(image, "n_frames", 1)
            image.verify()
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                "media_type": media_type, "width": width, "height": height, "frame_count": frames}
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise NativeImageError("image_invalid") from exc


def validate_masked_inputs(source: bytes, mask: bytes) -> None:
    """Reject unusable masks before either backend uploads or submits them."""
    decoded = []
    try:
        for raw in (source, mask):
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if (getattr(image, "n_frames", 1) != 1 or image.getexif().get(274, 1) not in (None, 1)
                        or image.mode not in {"RGB", "L"} or image.width * image.height > 40_000_000):
                    raise ValueError("unsupported masked image")
                decoded.append(image.copy())
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise NativeImageError("edit_mask_invalid") from exc

    source_image, mask_image = decoded
    if source_image.size != mask_image.size:
        raise NativeImageError("mask_dimension_mismatch")
    if mask_image.mode == "RGB":
        red, green, blue = mask_image.split()
        if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
            raise NativeImageError("edit_mask_invalid")
        mask_image = red
    low, high = mask_image.getextrema()
    if low != 0 or high == 0:
        raise NativeImageError("edit_mask_empty")


@dataclass(frozen=True, slots=True)
class ReferenceImage:
    """Only the trusted host can resolve a reference to original bytes."""

    reference_id: str
    data: bytes = field(repr=False)
    source_key: str
    sha256: str
    role: str = "edit_source"

    def metadata(self) -> dict:
        require_text(self.reference_id, "reference_id")
        require_text(self.source_key, "reference_source")
        if self.role not in {"identity", "outfit", "pose", "scene", "style", "background",
                             "continuity", "edit_source", "edit_mask"}:
            raise NativeImageError("invalid_reference_role")
        info = inspect_image(self.data)
        if self.sha256 != info["sha256"]:
            raise NativeImageError("reference_fingerprint_mismatch")
        return {"reference_id": self.reference_id, "source_key": self.source_key, "role": self.role, **info}


@dataclass(frozen=True, slots=True)
class ReferenceOffer:
    """Hash-bound metadata persisted before the host releases one-use bytes."""

    reference_id: str
    source_key: str
    sha256: str
    role: str = "edit_source"

    def metadata(self) -> dict:
        require_text(self.reference_id, "reference_id")
        require_text(self.source_key, "reference_source")
        if (len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256)
                or self.role not in {"identity", "outfit", "pose", "scene", "style", "background",
                                     "continuity", "edit_source", "edit_mask"}):
            raise NativeImageError("invalid_reference_offer")
        return {"reference_id": self.reference_id, "source_key": self.source_key,
                "sha256": self.sha256, "role": self.role}


@dataclass(frozen=True, slots=True)
class ImageInvocation:
    scope_key: str
    request_id: str
    invocation_id: str
    origin_key: str
    operation: str
    prompt: str
    negative_prompt: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    reference_ids: tuple[str, ...] = ()
    envelope: Mapping[str, Any] = field(default_factory=dict)
    model_call: Callable | None = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        for name in ("scope_key", "request_id", "invocation_id", "origin_key", "prompt"):
            require_text(getattr(self, name), name)
        if self.operation not in {"generate", "edit", "mask_edit"}:
            raise NativeImageError("unsupported_image_operation")
        if not isinstance(self.negative_prompt, str):
            raise NativeImageError("invalid_negative_prompt")
        if not isinstance(self.parameters, Mapping):
            raise NativeImageError("invalid_image_parameters")
        params = json.loads(document(dict(self.parameters)))
        allowed = {"width", "height", "steps", "cfg_scale", "seed", "batch_size",
                   "sampler_name", "scheduler", "denoising_strength"}
        if params.keys() - allowed:
            raise NativeImageError("unsupported_image_parameter")
        for name, value in params.items():
            if name in {"sampler_name", "scheduler"}:
                require_text(value, name)
            elif name in {"cfg_scale", "denoising_strength"}:
                if type(value) not in {int, float} or value < 0:
                    raise NativeImageError("invalid_image_parameter")
            elif type(value) is not int or value < (-1 if name == "seed" else 1):
                raise NativeImageError("invalid_image_parameter")
        references = tuple(self.reference_ids)
        if any(not isinstance(value, str) or not value for value in references) or len(set(references)) != len(references):
            raise NativeImageError("invalid_reference_ids")
        if self.operation == "edit" and len(references) > 8:
            raise NativeImageError("edit_reference_limit")
        if self.operation == "mask_edit" and len(references) not in (0, 2):
            raise NativeImageError("mask_edit_requires_source_and_mask")
        if self.operation == "generate" and references:
            raise NativeImageError("generate_does_not_accept_references")
        object.__setattr__(self, "parameters", MappingProxyType(params))
        object.__setattr__(self, "reference_ids", references)
        object.__setattr__(self, "envelope", MappingProxyType(json.loads(document(dict(self.envelope)))))

    def document(self, references: tuple[ReferenceImage, ...], offers: tuple[ReferenceOffer, ...] = ()) -> dict:
        if references and offers:
            raise NativeImageError("reference_binding_mismatch")
        metadata = tuple(value.metadata() for value in references) if references else tuple(value.metadata() for value in offers)
        if tuple(item["reference_id"] for item in metadata) != self.reference_ids:
            raise NativeImageError("reference_binding_mismatch")
        roles = tuple(reference["role"] for reference in metadata)
        if self.operation == "mask_edit" and roles and (roles.count("edit_source") != 1 or roles.count("edit_mask") != 1):
            raise NativeImageError("invalid_mask_reference_roles")
        return {"scope_key": self.scope_key, "request_id": self.request_id, "invocation_id": self.invocation_id,
                "origin_key": self.origin_key, "operation": self.operation, "prompt": self.prompt,
                "negative_prompt": self.negative_prompt, "parameters": dict(self.parameters),
                "references": list(metadata), "envelope": dict(self.envelope)}


@dataclass(frozen=True, slots=True)
class BackendResult:
    """Known outputs or a confirmed remote task; absence of either is unknown."""

    images: tuple[bytes, ...] = ()
    locator: Mapping[str, Any] = field(default_factory=dict)
    receipt: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    metadata: Mapping[str, Any]
    data: bytes = field(repr=False)
