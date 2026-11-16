"""Candidate routing INSIDE the existing proactive outbox dispatch.

No new task/sender or second logical delivery. Only trusted `not_sent` receipts
permit another account. accepted/unknown/exceptions pin the dispatch; no guessing.
An attempt is journaled before entering the sender and survives process death.
"""
from dataclasses import replace
from datetime import datetime
import json
import logging
import random

from companion_v2.delivery.models import DeliveryReceipt
from companion_v2.execution.models import TaskContext, TaskError, encode, require, stamp
from companion_v2.proactive.configuration import GROUP
from .account_links import PrivateAccountLinks, member_for, member_key

PREFIX = "account-link-delivery:"
CONTACT_PREFIX = "account-link-contact:"
REJECTION_PREFIX = "account-link-rejections:"
logger = logging.getLogger(__name__)


def weighted_order(contexts, weights, rng):
    """Weighted draws without replacement, after all local eligibility checks.

    The first account has weight / total eligible weight probability. Confirmed
    not-sent fallbacks draw from the remaining positive-weight accounts only.
    """
    remaining = [(context, weights.get(member_key(member_for(context)), 1)) for context in contexts]
    remaining = [(context, weight) for context, weight in remaining if weight > 0]
    result = []
    while remaining:
        ticket = rng.randrange(sum(weight for _, weight in remaining))
        for index, (context, weight) in enumerate(remaining):
            if ticket < weight:
                result.append(context)
                remaining.pop(index)
                break
            ticket -= weight
    return result


class PrivateAccountDelivery:
    def __init__(self, application, resolve, *, available=None, rng=None):
        self.app, self.runtime, self.resolve = application, application.runtime, resolve
        self.available = available or (lambda context: True)
        self.rng = rng or random.SystemRandom()

    def _read(self, key):
        row = self.runtime.read_one("SELECT value FROM runtime_metadata WHERE key=?", (key,))
        return json.loads(row["value"]) if row else None

    @staticmethod
    def _put(connection, key, value):
        connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES (?,?)", (key, encode(value)))

    def _group(self, original):
        group = PrivateAccountLinks(self.runtime).group_for(original)
        check = getattr(self.app, "private_account_link_persona", None)
        if not group or not check or check(member_for(original)) != group["persona_id"]:
            return None
        return group if group.get("proactive", {}).get("mode", "off") != "off" else None

    def _eligible(self, context, original, group, purpose, logical_key):
        current = self._group(original)
        check = self.app.private_account_link_persona
        member = member_for(context)
        if (not current or current["id"] != group["id"] or current["proactive"] != group["proactive"] or member not in current["members"]
                or check(member) != group["persona_id"]):
            raise TaskError("account_link_route_changed", "association or persona changed before dispatch")
        self.app.tasks._context(context, self.app.now())
        for action in ("proactive.contact", "conversation.reply"):
            require(self.app.tasks.check_context_access(context, action=action, observed_at=self.app.now()))
        # This includes the original per-account local OFF/quiet/pause veto.
        preferences = require(self.app.proactive.preferences.read(context, observed_at=self.app.now()))
        preferences.require_contact(purpose)
        # Trust only the persisted source contact, never a request/UI flag.
        row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id=?", (logical_key,))
        administrator_test = row is not None and json.loads(row["record_json"]).get("administrator_test") is True
        if not administrator_test:
            self.app.proactive.budget.contacts(context, preferences, self.app.now(), exclude_contact_id=logical_key)
        if not self.available(context):
            raise TaskError("account_link_transport_unavailable", "account transport is unavailable")
        return context

    def _candidates(self, original, group, purpose, request, *, rejections=None):
        pinned = self._read(CONTACT_PREFIX + request.logical_key)
        # An accepted/unknown/delivered part pins all remaining parts, even if
        # that account's random weight is now zero. Never split across platforms.
        members = [pinned["member"]] if pinned else list(group["members"])
        policy = group["proactive"]
        weights = {member_key(entry["member"]): entry["weight"] for entry in policy.get("weights", [])}
        result = []
        rejections = [] if rejections is None else rejections
        for member in members:
            if not pinned and policy["mode"] == "random" and weights.get(member_key(member), 1) == 0:
                rejections.append({"member": member, "code": "account_link_weight_zero"})
                continue
            try:
                context = self.resolve(member)
                self._eligible(context, original, group, purpose, request.logical_key)
                result.append(context)
            except (TaskError, ValueError, KeyError, TypeError) as error:
                rejections.append({"member": member, "code": error.code if isinstance(error, TaskError) else "account_link_context_unavailable"})
                continue
        if not pinned and policy["mode"] == "random":
            if "weights" not in policy:
                # Existing saved random policies retain uniform selection.
                self.rng.shuffle(result)
            else:
                result = weighted_order(result, weights, self.rng)
        elif not pinned:
            preferred = policy.get("preferred")
            result.sort(key=lambda context: member_for(context) != preferred)
        return result

    def _receipt(self, envelope, failure):
        return DeliveryReceipt(envelope.part_id, envelope.operation_id, envelope.context.key,
            envelope.payload_hash, envelope.sender, "not_sent", "linked-not-sent:" + envelope.operation_id,
            failure_code=failure)

    def _pinned_diagnostic(self, request):
        if request.owner != GROUP:
            return False
        row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id=?", (request.source_ref,))
        data = json.loads(row["record_json"]) if row else {}
        return data.get("administrator_test") is True and data.get("diagnostic_allow_linked_accounts") is not True

    def rejection_report(self, delivery_id):
        report = self._read(REJECTION_PREFIX + delivery_id)
        return report.get("rejections", []) if report else []

    def _save_rejections(self, delivery_id, rejections):
        # Separate from the wire journal: a readiness rejection is NOT a send.
        with self.runtime.transaction() as connection:
            self._put(connection, REJECTION_PREFIX + delivery_id,
                      {"at": stamp(self.app.now()), "rejections": rejections})

    def ready(self, request, *, delivery_id=None):
        if request.owner != GROUP or self._pinned_diagnostic(request):
            return True
        group = self._group(request.context)
        if not group:
            return True
        row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id=?", (request.source_ref,))
        if row is None:
            raise TaskError("contact_draft_unavailable", "retain the existing proactive source owner")
        purpose = json.loads(row["record_json"])["purpose"]
        rejections = []
        candidates = self._candidates(request.context, group, purpose, request, rejections=rejections)
        if delivery_id is not None:
            self._save_rejections(delivery_id, rejections)
            if not candidates:
                logger.warning("V2 linked route unavailable delivery=%s rejections=%s", delivery_id,
                               encode(rejections))
        return bool(candidates)

    def authorize_media(self, envelope):
        original = getattr(envelope, "linked_private_source", None)
        if original is None:
            return
        journal = self._read(PREFIX + envelope.part_id)
        group = self._group(original)
        if (not journal or journal["state"] != "dispatching"
                or journal["operation_id"] != envelope.operation_id
                or journal["target_key"] != envelope.context.key
                or journal["source_key"] != original.key
                or not group or group["id"] != journal["group_id"]
                or member_for(envelope.context) not in group["members"]):
            raise TaskError("account_link_media_authority_required", "linked private media needs the original proactive outbox authority")

    def normalize_receipt(self, receipt):
        """Trusted late receipts retain their actual route proof and original outbox ID."""
        key = PREFIX + receipt.part_id
        journal = self._read(key)
        if journal is None:
            return receipt
        if receipt.operation_id != journal["operation_id"] or receipt.origin_key not in (journal["source_key"], journal["target_key"]):
            raise TaskError("delivery_receipt_mismatch", "receipt differs from the journaled routed dispatch")
        normalized = replace(receipt, origin_key=journal["source_key"])
        current = self.runtime.read_one("SELECT state FROM background_deliveries WHERE delivery_id=?", (receipt.part_id,))
        if current and current["state"] == "delivered" and receipt.status in ("unknown", "accepted"):
            return normalized
        # Called within record_receipt's original transaction. A conflicting
        # receipt rolls this audit update back together with the outbox check.
        with self.runtime.transaction() as connection:
            journal.update(state=receipt.status, receipt=normalized)
            journal["attempts"][-1].update(state=receipt.status,
                receipt=replace(receipt, origin_key=journal["target_key"]))
            self._put(connection, key, journal)
            if receipt.status != "not_sent":
                self._put(connection, CONTACT_PREFIX + journal["logical_key"],
                    {"group_id": journal["group_id"], "member": journal["attempts"][-1]["member"], "state": receipt.status})
        return normalized

    def actual_context(self, delivery_id):
        journal = self._read(PREFIX + delivery_id)
        return TaskContext.restore(journal["target_context"]) if journal and journal.get("target_context") else None

    async def send(self, envelope, request, sender):
        if request.owner != GROUP or self._pinned_diagnostic(request):
            # Without explicit forwarding consent, administrator diagnostics stay
            # on the reviewed recipient. Opt-in retains linked policy except test quota.
            return await sender.send(envelope)
        group = self._group(envelope.context)
        if not group:
            return await sender.send(envelope)
        key = PREFIX + envelope.part_id
        previous = self._read(key)
        if previous:
            if previous.get("operation_id") != envelope.operation_id:
                raise TaskError("account_link_dispatch_conflict", "dispatch journal belongs to another operation")
            if previous.get("receipt"):
                data = previous["receipt"]
                from companion_v2.execution.models import ProviderBinding
                data = {**data, "sender": ProviderBinding(**data["sender"]),
                        "delivered_at": datetime.fromisoformat(data["delivered_at"]) if data.get("delivered_at") else None}
                return DeliveryReceipt(**data)
            # Crash/timeout after entering a sender is never evidence of not sent.
            raise TaskError("account_link_delivery_unresolved", "dispatch already entered a sender; reconcile it")
        row = self.runtime.read_one("SELECT record_json FROM proactive_contacts WHERE contact_id=?", (request.source_ref,))
        if row is None:
            raise TaskError("contact_draft_unavailable", "retain the existing proactive source owner")
        purpose = json.loads(row["record_json"])["purpose"]
        rejections = []
        candidates = self._candidates(envelope.context, group, purpose, request, rejections=rejections)
        self._save_rejections(envelope.part_id, rejections)
        if not candidates:
            return self._receipt(envelope, "account_link_all_unavailable")
        attempts = []
        normalized = self._receipt(envelope, "account_link_all_unavailable")
        for context in candidates:
            # Image manifests remain owned by their source; explicit link scope
            # authorization is carried separately and rechecked at the wire fence.
            def fence(target=context):
                if envelope.before_dispatch is None:
                    raise TaskError("dispatch_fence_missing", "retain original task dispatch fence")
                envelope.before_dispatch()
                self._eligible(target, envelope.context, group, purpose, request.logical_key)
            try:
                fence()
            except TaskError as error:
                rejections.append({"member": member_for(context), "code": error.code})
                self._save_rejections(envelope.part_id, rejections)
                continue
            extras = {"linked_private_source": envelope.context} if getattr(envelope, "images", ()) and context.scope != envelope.context.scope else {}
            actual = replace(envelope, context=context, before_dispatch=fence, **extras)
            attempts.append({"member": member_for(context), "target_key": context.key, "state": "dispatching"})
            journal = {"operation_id": envelope.operation_id, "logical_key": request.logical_key,
                "group_id": group["id"], "source_key": envelope.context.key, "target_key": context.key,
                "at": stamp(self.app.now()), "state": "dispatching", "attempts": attempts, "target_context": json.loads(encode(context))}
            with self.runtime.transaction() as connection:
                # No other task can allocate the same dispatch between journal check and insert.
                existing = self._read(key)
                if existing is not None and len(attempts) == 1:
                    raise TaskError("account_link_delivery_unresolved", "another runner already entered this dispatch")
                self._put(connection, key, journal)
            receipt = await sender.send(actual)
            if (not isinstance(receipt, DeliveryReceipt) or
                    (receipt.part_id, receipt.operation_id, receipt.origin_key, receipt.payload_hash, receipt.sender) !=
                    (actual.part_id, actual.operation_id, actual.context.key, actual.payload_hash, actual.sender)):
                raise TaskError("delivery_receipt_mismatch", "route sender returned another envelope's receipt")
            attempts[-1].update(state=receipt.status, receipt=receipt)
            if receipt.status == "not_sent":
                rejections.append({"member": member_for(context), "code": receipt.failure_code or "delivery_not_sent"})
                self._save_rejections(envelope.part_id, rejections)
            # The outbox retains its source-owned identity. Actual target and the
            # unmodified trusted receipt remain in the durable audit journal.
            normalized = replace(receipt, origin_key=envelope.context.key)
            journal.update(state=receipt.status, receipt=normalized)
            with self.runtime.transaction() as connection:
                self._put(connection, key, journal)
                if receipt.status != "not_sent":
                    self._put(connection, CONTACT_PREFIX + request.logical_key,
                        {"group_id": group["id"], "member": member_for(context), "state": receipt.status})
            if receipt.status != "not_sent":
                return normalized
        return normalized
