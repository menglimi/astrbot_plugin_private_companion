"""Trusted, bounded inputs to the supervised contact worker."""

from dataclasses import dataclass
from datetime import datetime

from companion_v2.execution.models import TaskContext
from companion_v2.conversation.voice_reply import VoiceReplyPolicy

from .configuration import PURPOSES
from .models import Thought, ThoughtKind


@dataclass(frozen=True)
class ContactIntent:
    purpose: str
    thought: Thought

    def __post_init__(self):
        if self.purpose not in PURPOSES or not isinstance(self.thought, Thought):
            raise ValueError("contact intent requires a registered purpose and thought")
        if self.thought.kind is not ThoughtKind.CONVERSATION or self.thought.evidence.assets:
            raise ValueError("contact text worker needs text evidence; attachments require their own delivery owner")
        if len(self.thought.summary) > 8192 or len(self.thought.evidence.sources) > 16:
            raise ValueError("contact inputs must be bounded")


@dataclass(frozen=True)
class ContactJob:
    context: TaskContext
    intent: ContactIntent
    role_instruction: str
    request_hash: str
    preference_token: str
    source_hashes: tuple[str, ...]
    feasible_count: int
    weight_used: bool
    history: dict | None = None
    interaction: dict | None = None
    automatic_origin: dict | None = None
    last_reply: dict | None = None
    presentation_settings: dict | None = None
    segmentation_settings: dict | None = None
    # The exact task deadline the contact must finish inside. Optional memory
    # recall borrows it as the query-session expiry so a session can never
    # outlive the message it was captured for.
    deadline_at: datetime | None = None
    administrator_test: bool = False
    diagnostic_source_backed: bool = False
    diagnostic_origin: dict | None = None
    expression_style: dict | None = None
    dialogue_history: dict | None = None
    diagnostic_image_intent: str | None = None
    voice_policy: VoiceReplyPolicy | None = None
    plan: dict | None = None
    plan_call_id: str | None = None
    plan_usage_known: bool = False
