"""Seal outgoing image choices as governed sources, never URLs or copied bytes."""

from dataclasses import dataclass
from datetime import datetime
import json

from companion_v2.conversation.foreground import ModelDraft
from companion_v2.delivery.media import VERSION, image_manifest, payload_hash
from companion_v2.execution.models import TaskError, digest, encode, require
from companion_v2.foundation.refs import ScopeKind, SourceRef, VersionRef
from companion_v2.sources.dependencies import link
from companion_v2.sources.models import SourceRecord
from .models import MAX_INPUT_IMAGES


OWNER = "conversation.reply_images"
INSTRUCTION = """需附上本轮可发送原图时，在最终 JSON 的 images 中按顺序填 sendable_image 句柄；不用图则省略或填空数组。轻松即时且图片足够表达时，可只发图（text=""，claims=[]）；需要答案、解释、安慰或重要细节时仍说清文字，不无意义地把所有收图发回。
带有 image_origin=user_input 的图片来自用户的当前输入、引用或转发，只用于理解用户提供的内容，不是角色的照片来源，也不代表角色能访问用户手机相册或图库。sendable_image 只表示原件在技术上可回送，不代表本轮适合或获准回送；默认不要选择 user_input 图片。用户对角色说“来张照片”“看看照片”“发一张你的照片/自拍/穿搭照”时，默认是在要角色自己的照片，不是在要求重发当前或历史收到的用户图片；尤其不得把用户手机相册上传的照片冒充成角色照片。不要声称从角色相册“找到/翻到”照片，除非本轮已授权的角色素材或图片工具确实提供了它。没有合适的角色素材或本轮已授权生成/编辑产物时，用文字如实说明。只有用户明确要求把同一张用户原图原样发回（例如“把刚才那张原图发回给我”）时，才可选择该原图。
只发送已提供的完整原件。生成或编辑先调用本轮授权工具，再选实际返回的产物，generated 保留生成/编辑归因；动画发送原文件，不表示模型看完了未提供的帧。群聊图面向当前群，引用/转发保留原发送者与显示归因，不表示角色拍摄、创作或认同。
images 不填 URL、文件路径、base64、文字证据或抽帧句柄。图片按当前策略随回复投递，未确认前不声称发送成功。"""


@dataclass(frozen=True)
class ImageReplyDraft(ModelDraft):
    images: tuple[SourceRef, ...] = ()

    def __post_init__(self):
        if not isinstance(self.text, str) or (self.text == "" and self.claims):
            raise TaskError("reply_images_invalid", "image-only replies require empty text and claims")
        if self.text:
            super().__post_init__()
        if (not isinstance(self.images, tuple) or not 1 <= len(self.images) <= MAX_INPUT_IMAGES
                or any(not isinstance(i, SourceRef) for i in self.images) or len(set(self.images)) != len(self.images)):
            raise TaskError("reply_images_invalid", "reply images require unique original image references")


def reply_manifest(batch_id, call_id, parts, images=(), *, image_position="after_text", action="text"):
    if action not in ("text", "image", "voice_only", "poke", "silent"):
        raise ValueError("reply manifest action is unsupported")
    items = [batch_id, call_id, parts]
    if action != "text":
        items.append({"action": action})
    if images:
        items.extend((VERSION, [image_manifest(i) for i in images]))
        if image_position != "after_text":
            items.append({"image_position": image_position})
    return digest(items)


class ConversationReplyImages:
    def __init__(self, messages):
        self.messages, self.runtime = messages, messages.runtime
        self.enabled = False

    def select(self, draft, request):
        references = getattr(draft, "images", ())
        if not references:
            return ()
        if (not isinstance(draft, ImageReplyDraft) or not self.enabled or not request.reply_images
                or request.event.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP)):
            raise TaskError("reply_images_unavailable", "this reply has no original image delivery contract")
        originals = {i.source: i for i in request.images if i.source.owner != "screen"}
        if any(ref not in originals for ref in references):
            raise TaskError("reply_images_unavailable", "only this request's original images may be attached")
        return tuple(originals[ref] for ref in references)

    @staticmethod
    def reference(batch, context):
        identifier = "reply-images-" + digest(batch["batch_id"])
        return SourceRef(identifier, OWNER, context.scope, VersionRef(identifier, 1), datetime.fromisoformat(batch["created_at"]))

    def publish(self, batch, context, part_id, images, at, *, image_position="after_text"):
        if not images:
            return
        if image_position not in ("after_text", "before_text"):
            raise ValueError("outbound image position is unsupported")
        reference = self.reference(batch, context)
        data = {"version": VERSION, "part_id": part_id, "images": [image_manifest(i) for i in images]}
        if image_position != "after_text":
            data["image_position"] = image_position
        payload = encode(data)
        require(self.messages.sources.publish(SourceRecord(reference, context.audience, digest(payload), payload), observed_at=at))
        link(self.runtime, reference, tuple(view.source for i in images for view in i.evidence_images), at)

    def _input_images(self, context, events, at):
        """Read only admitted originals; the group history owner checks ancestry."""
        images = []
        for event in events:
            images.extend(self.messages.images.read(context, (event,), at))
            if getattr(event, "quotes", ()):
                images.extend(self.messages.quotes.read_all(context, event, at)[1])
            if getattr(event, "forwards", ()):
                images.extend(self.messages.forwards.read_all(context, event, at)[1])
        return tuple(images)

    def read_binding(self, batch, context, at, *, request=None, input_events=None, with_position=False):
        """Return sealed originals, their source, and the part that owns them."""
        reference = self.reference(batch, context)
        exists = self.runtime.read_one("SELECT 1 FROM sources WHERE source_id = ? AND lineage_id = ? AND source_revision = 1",
                                       (reference.source_id, reference.version.lineage_id))
        parts = self.runtime.read_all("SELECT * FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch["batch_id"],))
        if not exists:
            if any(payload_hash(p["text"]) != p["payload_hash"] for p in parts):
                raise TaskError("reply_images_unavailable", "the reply payload has lost its original manifest")
            return ((), None, None, "after_text") if with_position else ((), None, None)
        if context.scope.kind not in (ScopeKind.PRIVATE, ScopeKind.GROUP):
            raise TaskError("reply_images_unavailable", "this image manifest requires its original conversation audience")
        record = self.messages._read_source(reference, context, "text", at)
        try:
            data = json.loads(record.payload)
            if (record.content_hash != digest(record.payload) or not isinstance(data, dict)
                    or set(data) not in ({"version", "part_id", "images"},
                                        {"version", "part_id", "images", "image_position"})
                    or data["version"] != VERSION
                    or not parts or data["part_id"] not in {part["part_id"] for part in parts}
                    or not isinstance(data["images"], list) or not 1 <= len(data["images"]) <= MAX_INPUT_IMAGES):
                raise ValueError("invalid manifest")
            image_position = data.get("image_position", "after_text")
            if image_position not in ("after_text", "before_text"):
                raise ValueError("invalid image position")
            if input_events is not None:
                # The bounded group reader already walks the saved context and
                # tool dependencies. Do not recursively compile that context a
                # second time just to resolve the outgoing image selection.
                available = self._input_images(context, input_events, at)
            else:
                request = request or self.messages._request(batch, context, at, include_tools=False, historical=True)
                available = request.images
            generated = self.messages.generated_images.read_batch(batch, context, at)
            semantic = getattr(self.messages, "semantics", None)
            if semantic is not None:
                generated += semantic.reactions.read_batch(batch, context)[0]
            available = tuple(image for image in available if image.source.owner != "screen")
            offered = {image.source for image in available}
            available += tuple(image for image in generated if image.source not in offered)
            originals = {encode(image_manifest(i)): i for i in available}
            images = tuple(originals[encode(item)] for item in data["images"])
            if (len({i.source for i in images}) != len(images)
                    or any(payload_hash(p["text"], images if p["part_id"] == data["part_id"] else (),
                                        image_position=image_position) != p["payload_hash"] for p in parts)
                    or reply_manifest(batch["batch_id"], batch["call_id"], tuple(p["text"] for p in parts), images,
                                      image_position=image_position,
                                      action=batch["presentation_action"]) != batch["manifest_hash"]):
                raise ValueError("changed manifest")
        except TaskError:
            raise
        except (ValueError, TypeError, KeyError):
            raise TaskError("reply_images_changed", "outgoing image selection differs from its sealed originals") from None
        result = images, reference, data["part_id"]
        return (*result, image_position) if with_position else result

    def read(self, batch, context, at, *, request=None, input_events=None):
        images, reference, _ = self.read_binding(batch, context, at, request=request, input_events=input_events)
        return images, reference

    def for_part(self, part, batch, context, at, *, request=None, input_events=None, with_position=False):
        images, reference, part_id, position = self.read_binding(batch, context, at, request=request,
                                                               input_events=input_events, with_position=True)
        if part["part_id"] != part_id:
            return ((), None, "after_text") if with_position else ((), None)
        return (images, reference, position) if with_position else (images, reference)

    @staticmethod
    def describe(images):
        return [{"source_id": i.source.source_id, "mime_type": i.descriptor.mime_type,
                 "width": i.descriptor.width, "height": i.descriptor.height,
                 "byte_count": i.descriptor.byte_count, "content_hash": i.descriptor.content_hash} for i in images]

    def project_parts(self, parts, batch, context, at, *, request=None):
        images, _, part_id = self.read_binding(batch, context, at, request=request)
        return [{**dict(p), **({"images": self.describe(images)} if images and p["part_id"] == part_id else {})}
                for p in parts]
