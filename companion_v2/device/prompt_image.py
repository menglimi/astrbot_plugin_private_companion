"""A phone screenshot as a model prompt image, without inventing a chat source.

The screenshot is captured inside one supervised phone step.  It has no inbound
message and no user-visible source record, so it deliberately reports no
``evidence_images``: the request contract then does not pretend this image came
from the conversation.  The bytes and descriptor are still checked exactly like
any other prompt image.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64

from companion_v2.conversation.media.models import CapturedImage, ImageDescriptor, PromptImage
from companion_v2.execution.models import TaskError
from companion_v2.foundation.refs import SourceRef

OWNER = "device.phone"


@dataclass(frozen=True)
class PhonePromptImage(PromptImage):
    def __post_init__(self) -> None:
        if (not isinstance(self.source, SourceRef) or not isinstance(self.parent, SourceRef)
                or self.source.owner != OWNER or self.parent.owner != OWNER
                or self.source.scope != self.parent.scope
                or type(self.descriptor) is not ImageDescriptor
                or type(self.data_base64) is not str):
            raise TaskError("phone_image_invalid", "phone screenshot must retain its own source and bytes")
        try:
            CapturedImage(self.descriptor, base64.b64decode(self.data_base64, validate=True))
        except (ValueError, TypeError, TaskError):
            raise TaskError("phone_image_invalid", "phone screenshot bytes do not match the descriptor") from None

    @property
    def evidence_images(self) -> tuple:
        """A device capture is material, not an original conversation image."""
        return ()
