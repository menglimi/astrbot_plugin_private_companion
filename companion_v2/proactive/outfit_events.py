"""A fresh, role-owned daily outfit photo can back a private life share."""

import base64
from datetime import datetime, timedelta

from companion_v2.conversation.media.models import ImageDescriptor, PromptImage
from companion_v2.execution.models import TaskError, digest, encode, require, stamp
from companion_v2.foundation.refs import SourceRef, VersionRef
from companion_v2.references.daily_outfit import load_daily_outfit_record
from companion_v2.relationships.private_access import REVOCABLE_ABSENCE_CODES
from companion_v2.sources.models import SourceRecord

from .contact_models import ContactIntent
from .models import EvidenceSnapshot, Opportunity, Thought, ThoughtKind


OUTFIT_SOURCE_OWNER = "proactive.daily_outfit"


class OutfitContactEvents:
    def __init__(self, contacts, photos):
        self.contacts, self.photos = contacts, photos
        self.runtime = contacts.runtime

    @staticmethod
    def _reference(context, photo):
        identifier = "outfit-source-" + digest([context.key, photo["image_id"], photo["content_hash"]])
        return SourceRef(identifier, OUTFIT_SOURCE_OWNER, context.scope, VersionRef(identifier, 1),
                         datetime.fromisoformat(photo["created_at"]))

    @staticmethod
    def _marker(photo, expires):
        return {"image_id": photo["image_id"], "content_hash": photo["content_hash"],
                "purpose": "life", "expires_at": stamp(expires)}

    def select(self, context, preferences, at):
        if preferences.reason("life") or not self.contacts.delivery.sender.supports_images:
            return None, False
        try:
            photo = self.photos.share_photo(context, at)
        except TaskError as error:
            if error.code in REVOCABLE_ABSENCE_CODES | {"daily_outfit_disabled", "daily_outfit_unavailable"}:
                return None, False
            raise
        if photo is None:
            return None, False
        ref = self._reference(context, photo)
        expires = ref.issued_at + timedelta(seconds=preferences.values["event_max_age_seconds"])
        if not ref.issued_at <= at < expires:
            return None, False
        opportunity = Opportunity("outfit-" + digest([context.key, photo["local_date"]]),
                                  context.scope, ref.issued_at, expires)
        task_id = self.contacts.task_id(context, opportunity)
        if (self.runtime.read_one("SELECT 1 FROM execution_tasks WHERE task_id=?", (task_id,))
                or self.runtime.read_one("SELECT 1 FROM proactive_contacts WHERE task_id=?", (task_id,))):
            return None, False
        marker = self._marker(photo, expires)
        payload = encode({"kind": "当天生成的角色穿搭图", "local_date": photo["local_date"],
                          "weather": photo.get("weather"), "planned_activities": photo.get("activities", [])})
        require(self.contacts.sources.publish(SourceRecord(
            ref, context.audience, digest(payload), payload), observed_at=at))
        thought = Thought(opportunity.opportunity_id, opportunity, ThoughtKind.CONVERSATION,
            "角色刚完成今天的穿搭图。适合时顺手分享一个搭配灵感，用随口说话的语气。"
            "画面是角色的创作。行程只是安排，避免把它说成已经发生的经历。",
            EvidenceSnapshot(context.scope, at, (ref,)), 1.0)
        return (opportunity, (ContactIntent("life", thought),), marker), False

    def validate(self, context, marker, at):
        photo = self.photos.share_photo(context, at)
        if photo is None:
            raise TaskError("contact_outfit_unavailable", "the original outfit photo is unavailable")
        preferences = require(self.contacts.preferences.read(context, observed_at=at))
        created = datetime.fromisoformat(photo["created_at"])
        expires = created + timedelta(seconds=preferences.values["event_max_age_seconds"])
        if marker != self._marker(photo, expires) or not created <= at < expires:
            raise TaskError("contact_outfit_changed", "the original outfit share changed or expired")
        preferences.require_contact("life")
        return photo

    def image(self, context, marker, at):
        photo = self.validate(context, marker, at)
        original = load_daily_outfit_record(self.runtime, self.photos.app.references.owner(context), photo["image_id"])
        if original is None or original["content_hash"] != marker["content_hash"]:
            raise TaskError("contact_outfit_unavailable", "the original outfit bytes are unavailable")
        parent = self._reference(context, photo)
        identifier = parent.source_id + "-image"
        source = SourceRef(identifier, "conversation.image", context.scope, VersionRef(identifier, 1),
                           parent.issued_at)
        descriptor = ImageDescriptor(0, original["mime_type"], original["width"], original["height"],
                                     original["size_bytes"], original["content_hash"])
        return PromptImage(source, parent, descriptor, base64.b64encode(original["content"]).decode("ascii"))
