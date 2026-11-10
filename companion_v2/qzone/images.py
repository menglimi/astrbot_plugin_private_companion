"""Validated local image bytes and governed album images for public Zone posts."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError

from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.refs import RoleOwnerRef
from companion_v2.qzone.capability import PUBLISH_ACTION


MAX_IMAGES = 9
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PUBLISH_BODY = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 64 * 1024
MIME_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}


@dataclass(frozen=True)
class PublishImage:
    content: bytes = field(repr=False)
    mime_type: str
    width: int
    height: int
    content_hash: str
    source_id: str = ""

    @classmethod
    def read(cls, content, *, mime_type="", source_id=""):
        if not isinstance(content, bytes) or not content or len(content) > MAX_IMAGE_BYTES:
            raise TaskError("qzone_image_invalid", "图片为空或超过 12 MiB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as picture:
                    actual = Image.MIME.get(picture.format, "")
                    width, height = picture.size
                    if actual not in MIME_FORMATS or (mime_type and actual != mime_type):
                        raise ValueError("image format mismatch")
                    picture.verify()
                with Image.open(BytesIO(content)) as picture:
                    picture.load()
        except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            raise TaskError("qzone_image_invalid", "图片损坏或格式不支持，请选择 PNG、JPEG、WebP 或 GIF") from None
        return cls(content, actual, width, height, sha256(content).hexdigest(), source_id)

    @classmethod
    def from_data_url(cls, value):
        if type(value) is not str or len(value) > MAX_PUBLISH_BODY:
            raise TaskError("qzone_image_invalid", "图片数据无效或过大")
        header, separator, encoded = value.partition(",")
        mime = header.removeprefix("data:").removesuffix(";base64")
        if not separator or header != f"data:{mime};base64" or mime not in MIME_FORMATS:
            raise TaskError("qzone_image_invalid", "请选择 PNG、JPEG、WebP 或 GIF 图片")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise TaskError("qzone_image_invalid", "图片编码无效") from None
        return cls.read(content, mime_type=mime)


def image_batch(images):
    if not isinstance(images, (list, tuple)) or len(images) > MAX_IMAGES:
        raise TaskError("qzone_image_invalid", "每条说说最多选择 9 张图片")
    if any(not isinstance(item, PublishImage) for item in images):
        raise TaskError("qzone_image_invalid", "图片尚未通过读取校验")
    if sum(len(item.content) for item in images) > MAX_IMAGE_BYTES:
        raise TaskError("qzone_image_invalid", "所选图片合计不能超过 12 MiB，请缩小图片后再选")
    return tuple(images)


def album_images(application, context, at, image_ids, *, planner=None):
    """Resolve exact originals and recheck public-sharing consent at each call."""
    from companion_v2.sharing.planner import ShareCatalog, SharePlanner
    from companion_v2.sources.repositories import AssetRepository, SourceRepository

    if not isinstance(image_ids, (list, tuple)) or len(image_ids) > MAX_IMAGES:
        raise TaskError("qzone_image_invalid", "相册选图数量无效")
    if not image_ids:
        return (), ()
    planner = planner or SharePlanner(sources=SourceRepository(application.runtime),
        assets=AssetRepository(application.runtime), catalog=ShareCatalog())
    generated = application.messages.generated_images
    images, candidates = [], []
    for image_id in image_ids:
        item = generated.album_image(context, at, image_id)
        candidate = require(generated.album_share_candidate(item, context, planner=planner,
            capability=PUBLISH_ACTION, at=at, authorizer=application.tasks.identity))
        image = PublishImage.read(item.content, mime_type=item.descriptor.mime_type,
                                  source_id=candidate.material.source.source_id)
        if image.content_hash != candidate.material.asset.content_hash:
            raise TaskError("qzone_image_invalid", "相册图片与原始素材不一致")
        images.append(image)
        candidates.append(candidate.candidate_id)
    return image_batch(images), tuple(candidates)


def candidate_image(application, context, at, candidate_id, planner):
    """A prepared candidate still needs its exact governed bytes before upload."""
    material = require(planner.follow_up_material(candidate_id, resolved=context.actor,
        capability=PUBLISH_ACTION, observed_at=at, authorizer=application.tasks.identity,
        role_owner=RoleOwnerRef(context.scope.installation_id, context.scope.bot_id,
                               context.role_binding.role.subject_id)))
    if material.asset is None:
        raise TaskError("qzone_image_invalid", "分享候选没有可上传的图片")
    source, asset = material.source, material.asset
    row = application.runtime.read_one("""SELECT content FROM conversation_image_payloads
        WHERE source_id=? AND source_lineage=? AND source_revision=?
        AND asset_id=? AND asset_lineage=? AND asset_revision=?""",
        (source.source_id, source.version.lineage_id, source.version.revision,
         asset.asset_id, asset.version.lineage_id, asset.version.revision))
    if row is None:
        raise TaskError("qzone_image_invalid", "分享候选的图片原件已不可用")
    image = PublishImage.read(bytes(row["content"]), source_id=source.source_id)
    if image.content_hash != asset.content_hash:
        raise TaskError("qzone_image_invalid", "分享候选图片与原件不一致")
    return image
