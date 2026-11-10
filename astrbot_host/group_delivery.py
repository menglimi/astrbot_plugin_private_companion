"""Per-batch group delivery intents; configured groups no longer need a grant."""

from dataclasses import asdict

from companion_v2.execution.models import TaskError, stamp
from .group_settings import GroupTarget


CAPABILITY = "message.group.delivery"
INTENT_PREFIX = "host.multi.group.delivery.intent:"


class GroupDeliveryAuthority:
    def __init__(self, authority):
        self.authority = authority

    def target(self, target_id):
        target = self.authority.target(target_id)
        if not isinstance(target, GroupTarget):
            raise TaskError("host_group_target_required", "group delivery requires an explicit group target")
        return target

    def validate_target(self, target):
        known = self.authority.target(target.target_id)
        if known != target:
            template = self.authority.settings.template_for(target)
            if template is None:
                raise TaskError("host_group_target_required", "the group does not belong to a registered Bot account")
        return target

    def access(self, target):
        """正式版：群聊投递不再需要逐群授权；仍然要求这是一个配置里的群目标。"""
        self.validate_target(target)
        return {"capability": CAPABILITY, "target": asdict(target), "revision": 1,
                "issued_at": stamp(self.authority.now()), "expires_at": None, "revoked": False}

    def admit(self, target, context, batch_id, sender, *, replayed):
        authority = self.authority
        key = INTENT_PREFIX + batch_id
        if authority.read(key) is not None:
            return
        grant = self.access(target)
        authority.write(key, {"target": asdict(target), "origin_key": context.key, "mode": "onebot_group",
            "sender": asdict(sender), "grant_revision": grant["revision"]})

    def mode(self, batch_id):
        value = self.authority.read(INTENT_PREFIX + batch_id)
        return value["mode"] if value else "onebot_group"

    def authorize_envelope(self, target, envelope):
        authority = self.authority
        part = authority.runtime.read_one("SELECT batch_id FROM conversation_parts WHERE part_id = ?", (envelope.part_id,))
        intent = authority.read(INTENT_PREFIX + part["batch_id"]) if part else None
        grant = self.access(target)
        if (not intent or intent["mode"] != "onebot_group" or intent["target"] != asdict(target)
                or intent["origin_key"] != envelope.context.key or intent["sender"] != asdict(envelope.sender)):
            raise TaskError("host_group_delivery_intent_changed", "group delivery must retain its original admission")
        return grant
