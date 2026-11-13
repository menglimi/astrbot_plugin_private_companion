"""Private delivery of one image withheld from a group audience.

The channel is independent of the group turn: a different audience, a different
grant and a different receipt. It exists only when the host resolves an explicit,
separately authorized private target for the member who actually asked for the
image. A definite unsafe verdict always qualifies; an inconclusive verdict only
qualifies when its failure policy selects private delivery. Without an authorized
route the original group delivery remains image-free; no group audience is reused.

The destination and the exact withheld manifest are frozen into the review
delivery row when the fallback is queued, and rechecked on every read. A withdraw,
revoke or authority change therefore drops the whole delivery through the shared
source-dependency recheck that every dispatch already performs.
"""

from __future__ import annotations

from datetime import timedelta

from companion_v2.storage.image_payloads import load_image
import base64
import json

from companion_v2.foundation.refs import ScopeKind
from companion_v2.delivery.media import ImageDeliveryContent, manifest_hash
from companion_v2.delivery.service import DeliveryRequest
from companion_v2.execution.models import TaskContext, TaskError, digest, encode, require
from companion_v2.execution.service import outcome
from companion_v2.identity_policy.group_audience import group_audience

from .models import CapturedImage, ImageDescriptor, PromptImage
from .review import INCONCLUSIVE_LABELS, UNSAFE_LABELS, WITHHOLD, check_verdict


OWNER = "conversation.image_review_fallback"
VERSION = "conversation.group_image_fallback.v1"
MAX_LIFETIME_SECONDS = 300


class ImageReviewFallback:
    """Queue and re-validate the private half of a withheld group image."""

    def __init__(self, messages, delivery, *, port=None):
        self.messages, self.delivery, self.runtime = messages, delivery, messages.runtime
        self._port = port

    def attach_port(self, port):
        self._port = port

    @staticmethod
    def logical_key(review_key):
        return "image-review-fallback:" + digest([VERSION, review_key])

    # -- queueing ------------------------------------------------------

    def enqueue(self, part, *, deadline_at):
        """Queue this part's withheld image for the requester's private route."""
        plan = self.delivery.withheld_image(part)
        if plan is None:
            return None
        return self.queue(part, plan, deadline_at=deadline_at)

    def queue(self, part, plan, *, deadline_at):
        row, verdict = self._review(plan.review_key)
        target = verdict["target"]
        if target["part_id"] != part["part_id"]:
            raise TaskError("image_review_context", "reviewed fallback must use its original part")
        destination = self._destination(self._group_context(row))
        manifest = target["images"][0]
        document = {"version": VERSION, "review_key": plan.review_key, "part_id": part["part_id"],
                    "batch_id": row["batch_id"], "image_position": target["image_position"],
                    "manifest": manifest, "destination": json.loads(encode(destination))}
        content = manifest_hash("", (manifest,), image_position=target["image_position"])
        now = self.messages.tasks.now()
        end = min(deadline_at, now + timedelta(seconds=MAX_LIFETIME_SECONDS))
        if end <= now:
            raise TaskError("image_review_fallback_expired", "the original turn deadline has passed")
        with self.runtime.transaction():
            delivery = self.messages.image_review.substitution(part["part_id"])
            if delivery is None or delivery["mode"] != WITHHOLD:
                raise TaskError("image_review_decision", "this part did not withhold its image from the group")
            installed = self.messages.image_review.fallback_document(part["part_id"])
            if installed is not None:
                if {k: v for k, v in installed.items() if k != "delivery_id"} != document:
                    raise TaskError("image_review_fallback_conflict",
                                    "an original fallback cannot change its destination")
                return installed["delivery_id"]
            request = DeliveryRequest(OWNER, self.logical_key(plan.review_key), destination,
                                      plan.review_key, content, now, end)
            self.messages.image_review.install_fallback(part["part_id"], request.delivery_id, document)
        require(self._service().queue(request))
        return request.delivery_id

    def _service(self):
        service = getattr(self.messages, "outbound_delivery", None)
        if service is None:
            raise TaskError("image_review_fallback_unavailable",
                            "the shared background delivery owner is not attached")
        return service

    # -- checks --------------------------------------------------------

    def _review(self, review_key):
        row = self.messages.image_review.row(review_key)
        if row is None or row["state"] == "pending":
            raise TaskError("image_review_unavailable", "the original review is unavailable")
        verdict = check_verdict(json.loads(row["review_json"]))
        if not self._private_fallback_allowed(row, verdict):
            raise TaskError("image_review_decision", "this review did not withhold the image from the group")
        return row, verdict

    def _private_fallback_allowed(self, row, verdict):
        if verdict["state"] == "reviewed" and verdict["label"] in UNSAFE_LABELS:
            return True
        if verdict["state"] not in ("reviewed", "unavailable") or verdict["label"] not in INCONCLUSIVE_LABELS:
            return False
        settings = self.messages.image_review.settings_for(self._group_context(row))
        return settings["group_nsfw_image_review_failure_action"] == "private"

    def _group_context(self, row):
        group = TaskContext.restore(json.loads(self.messages.batch(row["batch_id"])["context_json"]))
        if (group.key != row["origin_key"] or group.scope.kind is not ScopeKind.GROUP
                or group.audience != group_audience(group.scope, group.actor.subject)):
            raise TaskError("image_review_context", "reviewed fallback needs its original group context")
        return group

    def _destination(self, group):
        """The host's explicit, currently authorized private route, or nothing."""
        if self._port is None:
            raise TaskError("image_review_fallback_unavailable",
                            "this installation has no authorized private fallback route")
        document = self._port(group)
        for item in document if isinstance(document, (list, tuple)) else (document,):
            if item is None:
                continue
            context = TaskContext.restore(item) if isinstance(item, dict) else item
            if (isinstance(context, TaskContext) and context.key != group.key
                    and context.scope.kind is ScopeKind.PRIVATE
                    and context.audience.recipients == (context.actor.subject,)
                    and context.scope.installation_id == group.scope.installation_id
                    and context.scope.bot_id == group.scope.bot_id
                    and context.scope.account_id == group.scope.account_id):
                return context
        raise TaskError("image_review_fallback_unauthorized",
                        "the requesting member has no authorized private route")

    def _row(self, part_id):
        return self.messages.image_review.substitution(part_id)

    def _stored(self, request):
        if request.owner != OWNER:
            raise TaskError("image_review_fallback_unavailable", "this delivery is not the original fallback")
        document = self.messages.image_review.fallback_document(None, delivery_id=request.delivery_id)
        if document is None:
            raise TaskError("image_review_fallback_unavailable", "the original fallback is unavailable")
        delivery = self.messages.image_review.substitution(document["part_id"])
        if (delivery is None or delivery["mode"] != WITHHOLD or document["version"] != VERSION
                or document["review_key"] != request.source_ref
                or request.logical_key != self.logical_key(request.source_ref)):
            raise TaskError("image_review_fallback_unavailable", "this delivery is not the original fallback")
        if request.payload_hash != manifest_hash("", (document["manifest"],),
                                                 image_position=document["image_position"]):
            raise TaskError("image_review_fallback_changed", "the original fallback payload changed")
        return document

    # -- delivery owner -------------------------------------------------

    @outcome
    def read(self, request, at):
        """Recheck the whole decision before the private image may leave."""
        document = self._stored(request)
        review = self.messages.image_review.row(document["review_key"])
        if review is None or review["state"] == "pending":
            raise TaskError("image_review_unavailable", "the original review is unavailable")
        verdict = check_verdict(json.loads(review["review_json"]))
        if not self._private_fallback_allowed(review, verdict):
            raise TaskError("image_review_decision", "this review no longer withholds the image")
        group = self._group_context(review)
        destination = self._destination(group)
        if destination != request.context or json.loads(encode(destination)) != document["destination"]:
            raise TaskError("image_review_fallback_route", "the authorized private route changed")
        manifest = document["manifest"]
        return ImageDeliveryContent("", (self._image(manifest, at),), allow_cross_scope_images=True)

    def _image(self, manifest, at):
        """Rebuild one reviewed original from its governed blob, never from a URL."""
        descriptor = ImageDescriptor(**manifest["descriptor"])
        row = self.runtime.read_one("""SELECT * FROM conversation_image_payloads
            WHERE source_id = ? AND source_lineage = ? AND source_revision = ?""",
            (manifest["source"]["source_id"], manifest["source"]["version"]["lineage_id"],
             manifest["source"]["version"]["revision"]))
        if row is None or row["descriptor_json"] != encode(descriptor):
            raise TaskError("image_review_original", "the withheld provider original is unavailable")
        captured = CapturedImage(descriptor, load_image(self.runtime.data_root, row["content"]))
        from companion_v2.conversation.messages import source_from_document
        return PromptImage(source_from_document(manifest["source"]), source_from_document(manifest["parent"]),
                           descriptor, base64.b64encode(captured.content).decode("ascii"))

    def description(self, part_id):
        """The installed fallback decision, for diagnostics and management."""
        row = self._row(part_id)
        if row is None or row["fallback_json"] is None:
            return None
        document = json.loads(row["fallback_json"])
        return {"delivery_id": row["fallback_delivery_id"], "manifest": document["manifest"],
                "destination": document["destination"]}
