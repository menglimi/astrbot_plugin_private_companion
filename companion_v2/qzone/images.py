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
from companion_v2.references.daily_outfit import (
    DAILY_OUTFIT_IMAGE_PREFIX,
    list_daily_outfit_records,
    load_daily_outfit_record,
)
from companion_v2.references.service import owner_key as reference_owner_key
from companion_v2.storage.generated_album import hidden_album_ids
from companion_v2.storage.image_payloads import load_image


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


def _role_reference_owner(application, context):
    references = getattr(application, "references", None)
    owner_fn = getattr(references, "owner", None)
    if callable(owner_fn):
        return owner_fn(context)
    return reference_owner_key(context)


def _outfit_album_image(application, context, at, image_id):
    require(application.tasks.identity.authorize(
        context.actor, scope=context.scope, capability=PUBLISH_ACTION, observed_at=at))
    owner = _role_reference_owner(application, context)
    record = load_daily_outfit_record(application.runtime, owner, image_id)
    if record is None:
        raise TaskError("qzone_image_invalid", "相册图片不可用")
    image = PublishImage.read(record["content"], mime_type=record["mime_type"],
                              source_id=record["image_id"])
    if image.content_hash != record["content_hash"]:
        raise TaskError("qzone_image_invalid", "相册图片与原始素材不一致")
    return image


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
    generated = getattr(getattr(application, "messages", None), "generated_images", None)
    images, candidates = [], []
    for image_id in image_ids:
        if isinstance(image_id, str) and image_id.startswith(DAILY_OUTFIT_IMAGE_PREFIX):
            images.append(_outfit_album_image(application, context, at, image_id))
            candidates.append(image_id)
            continue
        if generated is None:
            raise TaskError("qzone_image_invalid", "相册图片服务不可用")
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


def _generated_summary(generated, context, at, item):
    excerpt = ""
    getter = getattr(generated, "album_prompt_excerpt", None)
    if callable(getter):
        try:
            excerpt = getter(context, at, item.image_id) or ""
        except Exception:
            excerpt = ""
    if excerpt:
        return excerpt
    labels = {"image.generate": "生成图片", "image.edit": "编辑图片", "image.mask_edit": "局部改图"}
    return labels.get(getattr(item, "action", ""), "相册图片")


def _outfit_summary(record):
    parts = [f"{record.get('local_date', '')} 每日穿搭".strip()]
    activities = [str(item).strip() for item in (record.get("activities") or ()) if str(item).strip()]
    if activities:
        parts.append("活动：" + "、".join(activities[:3]))
    weather = record.get("weather")
    if isinstance(weather, dict):
        condition = str(weather.get("condition") or "").strip()
        temp = weather.get("temperature_c")
        weather_text = condition
        if temp is not None:
            weather_text = f"{condition} {temp}°C".strip()
        if weather_text:
            parts.append("天气：" + weather_text)
    return "；".join(part for part in parts if part)[:160]


def list_album_candidates(application, context, at, *, limit=12):
    """Return bounded, metadata-only visible role album entries for post selection."""
    try:
        bound = max(1, min(int(limit or 12), 24))
    except (TypeError, ValueError):
        bound = 12
    runtime = getattr(application, "runtime", None)
    if runtime is None:
        return []
    try:
        hidden = hidden_album_ids(runtime, context)
    except Exception:
        hidden = set()
    entries = []
    seen = set()
    generated = getattr(getattr(application, "messages", None), "generated_images", None)
    if generated is not None:
        try:
            cursor = None
            seen_cursors = set()
            while len(entries) < bound:
                page = generated.album_page(context, at, limit=bound, cursor=cursor)
                for item in page.get("items") or ():
                    image_id = getattr(item, "image_id", "")
                    if not image_id or image_id in hidden or image_id in seen:
                        continue
                    seen.add(image_id)
                    descriptor = getattr(item, "descriptor", None)
                    entries.append({
                        "image_id": image_id,
                        "created_at": str(getattr(item, "created_at", "") or ""),
                        "action": str(getattr(item, "action", "") or ""),
                        "width": int(getattr(descriptor, "width", 0) or 0),
                        "height": int(getattr(descriptor, "height", 0) or 0),
                        "mime_type": str(getattr(descriptor, "mime_type", "") or ""),
                        "summary": _generated_summary(generated, context, at, item),
                    })
                next_cursor = page.get("next_cursor")
                if not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        except TaskError:
            pass
    try:
        owner = _role_reference_owner(application, context)
        for record in list_daily_outfit_records(runtime, owner, hidden_ids=hidden):
            image_id = record["image_id"]
            if image_id in seen:
                continue
            seen.add(image_id)
            item = {
                "image_id": image_id,
                "created_at": str(record.get("created_at") or ""),
                "action": str(record.get("action") or ""),
                "width": int(record.get("width") or 0),
                "height": int(record.get("height") or 0),
                "mime_type": str(record.get("mime_type") or ""),
                "summary": _outfit_summary(record),
            }
            if record.get("local_date"):
                item["local_date"] = record["local_date"]
            if record.get("activities"):
                item["activities"] = list(record["activities"][:3])
            entries.append(item)
    except Exception:
        pass
    entries.sort(key=lambda item: (item["created_at"], item["image_id"]), reverse=True)
    return entries[:bound]


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
    try:
        content = load_image(getattr(application.runtime, "data_root", None), row["content"])
    except (OSError, TypeError, ValueError):
        raise TaskError("qzone_image_invalid", "分享候选的图片原件已不可用") from None
    image = PublishImage.read(content, source_id=source.source_id)
    if image.content_hash != asset.content_hash:
        raise TaskError("qzone_image_invalid", "分享候选图片与原件不一致")
    return image
