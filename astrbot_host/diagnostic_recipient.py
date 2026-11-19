"""Persist only the explicitly reviewed diagnostic recipient, not setup binding."""

import json
from pathlib import Path
from .setup_state import SetupStateStore


class DiagnosticRecipientStore(SetupStateStore):
    def __init__(self, data_root):
        self.root = Path(data_root).resolve() / "setup"
        self.path = self.root / "diagnostic-recipient.json"

    @staticmethod
    def identity(target):
        return {name: getattr(target, name) for name in
                ("target_id", "platform_id", "account_id", "bot_id", "participant_id")}

    def read(self):
        if not self.path.exists():
            return None
        raw = self.path.read_bytes()
        if len(raw) > 4096:
            raise ValueError("diagnostic recipient is invalid")
        value = json.loads(raw.decode("utf-8"))
        fields = {"target_id", "platform_id", "account_id", "bot_id", "participant_id"}
        if (type(value) is not dict or set(value) != fields
                or any(type(v) is not str or not v or len(v) > 512 for v in value.values())):
            raise ValueError("diagnostic recipient is invalid")
        return value
