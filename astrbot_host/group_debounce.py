"""Short, same-speaker text windows before native group reply admission."""

from collections.abc import Mapping

from companion_v2.conversation.private_debounce import MAX_MERGED_CHARS, Policy

from .private_debounce import PrivateDebounce


GROUP_POLICY = Policy(min_seconds=0.8, max_seconds=1.6, total_seconds=3.0)
MAX_GROUP_INPUTS = 16


def group_plain_text(event):
    """Only text-only group messages can safely share one native reply turn."""
    message = getattr(event, "message_obj", None)
    kind = getattr(getattr(message, "type", None), "value", getattr(message, "type", None))
    if kind != "GroupMessage" or getattr(event, "is_at_or_wake_command", False) is True:
        return None
    raw = getattr(message, "raw_message", None)
    original = raw.get("message") if isinstance(raw, Mapping) else None
    if isinstance(original, list) and any(
            not isinstance(part, Mapping) or part.get("type") != "text" for part in original):
        return None
    try:
        parts = tuple(message.message)
    except (AttributeError, TypeError):
        return None
    if not parts:
        return None
    texts = []
    for part in parts:
        part_kind = getattr(getattr(part, "type", None), "value", getattr(part, "type", None))
        value = getattr(part, "text", None)
        if part_kind != "Plain" or not isinstance(value, str):
            return None
        texts.append(value)
    text = "".join(texts)
    return text if text.strip() and len(text) <= MAX_MERGED_CHARS else None


def replace_group_plain_text(event, text):
    """Keep the latest native identity while presenting one complete text turn."""
    if group_plain_text(event) is None:
        return False
    try:
        part = event.message_obj.message[0]
        part.text = text
        event.message_obj.message = [part]
        event.message_obj.message_str = text
        event.message_str = text
    except (AttributeError, TypeError):
        return False
    return True


class GroupDebounce(PrivateDebounce):
    def __init__(self, policy=GROUP_POLICY, **kwargs):
        super().__init__(policy, log_enabled=False, **kwargs)
        self._active_senders = {}
        self._saturated = set()

    @staticmethod
    def _group_key(target_id, sender_id):
        return target_id + "\x00" + sender_id

    def observe(self, target_id, sender_id, *, eligible):
        """An intervening member or non-text event ends the prior text window."""
        previous = self._active_senders.get(target_id)
        if previous and (previous != sender_id or not eligible):
            self.flush(self._group_key(target_id, previous))
            self._active_senders.pop(target_id, None)
        if eligible and sender_id:
            self._active_senders[target_id] = sender_id

    def reserve_group(self, target_id, sender_id, event, text):
        if not target_id or not sender_id:
            return None
        self.observe(target_id, sender_id, eligible=True)
        key = self._group_key(target_id, sender_id)
        if key in self._saturated:
            return None
        pending = self._pending.get(key)
        if pending is not None and pending.force_ready:
            return None
        if pending is not None and pending.state == "active" and (
                len(pending.entries) >= MAX_GROUP_INPUTS
                or sum(len(item[1]) + 1 for item in pending.entries) + len(text) > MAX_MERGED_CHARS):
            self._saturated.add(key)
            self.flush(key)
            return None
        return self.reserve(key, event, text)

    async def finish(self, reservation, event):
        entries = await super().finish(reservation, event)
        if entries is not None:
            self._saturated.discard(reservation.key)
            target_id, _, sender_id = reservation.key.rpartition("\x00")
            if self._active_senders.get(target_id) == sender_id:
                self._active_senders.pop(target_id, None)
        return entries

    async def abort(self, reservation, *, event):
        aborted = await super().abort(reservation, event=event)
        if aborted:
            self._saturated.discard(reservation.key)
        return aborted

    async def close(self):
        self._active_senders.clear()
        self._saturated.clear()
        await super().close()
