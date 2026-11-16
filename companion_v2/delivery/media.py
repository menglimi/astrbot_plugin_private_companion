"""Image-bearing envelopes bind the ordered originals to the text and receipt."""

import base64
from dataclasses import dataclass, field
import json

from companion_v2.conversation.media.models import CapturedImage, PromptImage, fits_budget
from companion_v2.execution.models import TaskError, TaskContext, digest, encode
from companion_v2.foundation.refs import ScopeKind
from companion_v2.identity_policy.group_audience import group_audience
from .models import DeliveryEnvelope


VERSION = "conversation.reply_images.v1"


def image_manifest(image):
    return json.loads(encode({"source": image.source, "parent": image.parent, "descriptor": image.descriptor}))


def payload_hash(text, images=(), *, image_position="after_text"):
    return manifest_hash(text, tuple(image_manifest(i) for i in images), image_position=image_position)


def manifest_hash(text, manifests=(), *, image_position="after_text"):
    if image_position not in ("after_text", "before_text"):
        raise ValueError("outbound image position is unsupported")
    if not manifests:
        return digest(text)
    items = [VERSION, text, list(manifests)]
    if image_position != "after_text":
        items.append({"image_position": image_position})
    return digest(items)


@dataclass(frozen=True)
class ImageDeliveryContent:
    text: str
    images: tuple[PromptImage, ...]
    allow_cross_scope_images: bool = False

    def __post_init__(self):
        if (type(self.text) is not str or type(self.images) is not tuple
                or type(self.allow_cross_scope_images) is not bool):
            raise ValueError("image delivery content must be explicit and ordered")


@dataclass(frozen=True)
class ImageDeliveryEnvelope(DeliveryEnvelope):
    images: tuple[PromptImage, ...] = field(default=(), repr=False)
    image_position: str = "after_text"
    allow_cross_scope_images: bool = False
    linked_private_source: TaskContext | None = field(default=None, compare=False, kw_only=True)

    @staticmethod
    def _image_scope_valid(context, images, allow_cross_scope_images, linked_private_source=None):
        if any(not isinstance(image, PromptImage) for image in images):
            return False
        foreign = tuple(image for image in images if image.source.scope != context.scope)
        if not foreign:
            return not allow_cross_scope_images
        if linked_private_source is not None:
            return (isinstance(linked_private_source, TaskContext)
                and linked_private_source.scope.kind is ScopeKind.PRIVATE
                and context.scope.kind is ScopeKind.PRIVATE
                and context.role_binding.role == linked_private_source.role_binding.role
                and len(foreign) == len(images)
                and all(image.source.scope == linked_private_source.scope for image in images))
        scopes = {image.source.scope for image in foreign}
        return (allow_cross_scope_images and len(foreign) == len(images) and len(scopes) == 1
                and context.scope.kind is ScopeKind.PRIVATE
                and next(iter(scopes)).kind is ScopeKind.GROUP)

    def __post_init__(self):
        super().__post_init__()
        if (self.context.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP)
                or (self.context.scope.kind is ScopeKind.GROUP
                    and self.context.audience != group_audience(self.context.scope, self.context.actor.subject))
                or type(self.allow_cross_scope_images) is not bool
                or self.image_position not in ("after_text", "before_text")
                or not isinstance(self.images, tuple)
                or not self.images or not self._image_scope_valid(
                    self.context, self.images, self.allow_cross_scope_images, self.linked_private_source)
                or len({i.source for i in self.images}) != len(self.images)
                or not fits_budget(tuple(i.descriptor for i in self.images))
                or payload_hash(self.text, self.images, image_position=self.image_position) != self.payload_hash):
            raise TaskError("delivery_image_manifest_invalid", "image delivery must retain bounded ordered originals and text")
        for image in self.images:
            CapturedImage(image.descriptor, base64.b64decode(image.data_base64, validate=True))
