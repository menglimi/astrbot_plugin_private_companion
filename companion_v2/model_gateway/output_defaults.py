"""Registry defaults for each model task's output budget.

``model.max_output_tokens`` is a single island-wide number, and it was written
for a conversational reply. Structural tasks (learning, review, reflection,
planning, writing, ...) spend a large part of that same budget on reasoning
before they emit their JSON or body, so those kinds receive a thinking margin on
top of the host default. The result is only a registry default: a value the user
saved explicitly always wins, and routing clamps the default to the selected
provider's declared capability.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from .models import MAX_MODEL_OUTPUT_TOKENS


# kind -> (thinking factor, absolute floor). Kinds absent here keep the host
# default unchanged; that is the right answer for replies and for tasks whose
# whole output is one identifier or one verdict.
THINKING_MARGIN = MappingProxyType({
    # Structured bodies where reasoning dominated the visible output.
    "role.expression.learn": (4, 8192),
    "role.self_review": (4, 8192),
    "memory.reflect": (4, 8192),
    "content.authoring.draft": (4, 8192),
    # Structured bodies that are roughly half the size of the ones above.
    "role.expression.review": (2, 4096),
    "role.expression.contact_review": (2, 4096),
    "role.state.maintain": (2, 4096),
    "proactive.compose": (2, 4096),
    "proactive.plan": (1, 0),
    "role.journal.diary": (2, 4096),
    "role.journal.dream": (2, 4096),
    "role.journal.derive": (2, 4096),
    "role.growth.progress": (2, 4096),
    "role.reading.consume": (2, 4096),
    "calendar.plan": (2, 4096),
    "screen.interpretation": (2, 4096),
    # Nothing but an ID list or a pass/block verdict: no margin is warranted.
    "role.journal.select": (1, 0),
    "proactive.judge": (1, 0),
    # Replies stay deliberately short and low-latency.
    "conversation.reply": (1, 0),
    "conversation.poke_reply": (1, 0),
    "conversation.image_review": (1, 0),
    "conversation.image_narration": (1, 0),
    "conversation.video_narration": (1, 0),
    "conversation.context_image_caption": (1, 0),
})


def default_output_tokens(kind: str, base: int, *, maximum: int = MAX_MODEL_OUTPUT_TOKENS) -> int:
    """Return the registry default output ceiling for one task kind.

    ``base`` is the host-wide default (``model.max_output_tokens``); the product
    ceiling bounds every result, so a task default can never exceed it.
    """
    factor, floor = THINKING_MARGIN.get(kind, (1, 0))
    return max(1, min(maximum, max(floor, int(base) * factor)))


def with_thinking_margins(profiles):
    """Apply the table to freshly assembled task profiles.

    Only the registry default carried by each profile changes here; an explicit
    value saved by the user lives in configuration and keeps overriding it.
    """
    return tuple(replace(profile, max_output_tokens=default_output_tokens(profile.kind, profile.max_output_tokens))
                 for profile in profiles)
