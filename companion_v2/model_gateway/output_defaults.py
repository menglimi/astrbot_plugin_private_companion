"""Registry defaults for each model task's output budget.

``model.max_output_tokens`` is a single island-wide number, and it was written
for a conversational reply. Structured tasks (learning, review, reflection,
planning, writing, ...) spend a large part of that same budget on reasoning
before they emit their JSON or body, so every structured kind now registers a
flat 30000-token ceiling instead of a per-kind multiplier. Short tasks — replies,
pokes, verdicts, ID selection — keep their small budgets.

The result is only a registry default: a value the user saved explicitly always
wins, and routing clamps the default to the selected provider's declared
capability. That clamp is why a provider that only declares 8192 still gets
8192: the plugin never asks a provider for more than it says it can emit.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from .models import MAX_MODEL_OUTPUT_TOKENS


# 结构化任务统一 30000 输出上限。Provider 或产品上限更低时按低的算；用户把全局
# 上限显式设得更高时沿用用户的值。
STRUCTURED_OUTPUT_TOKENS = 30_000

STRUCTURED_KINDS = frozenset({
    "role.expression.learn",
    "role.self_review",
    "memory.reflect",
    "content.authoring.draft",
    "role.expression.review",
    "role.expression.contact_review",
    "role.state.maintain",
    "proactive.compose",
    "proactive.plan",
    "role.journal.diary",
    "role.journal.dream",
    "role.journal.derive",
    "role.growth.progress",
    "role.reading.consume",
    "calendar.plan",
    "screen.interpretation",
    "reality.text",
})

# kind -> (thinking factor, absolute floor). Short tasks keep the host default
# (factor 1, floor 0) unless they need a hard, small ceiling.
SHORT_TASK_BUDGETS = MappingProxyType({
    # Nothing but an ID list or a pass/block verdict.
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

# One registry of every task kind and the budget basis it carries; registration
# tests compare this set with the live profiles so a new task cannot quietly
# inherit the reply budget.
THINKING_MARGIN = MappingProxyType({
    **{kind: (1, STRUCTURED_OUTPUT_TOKENS) for kind in sorted(STRUCTURED_KINDS)},
    **SHORT_TASK_BUDGETS,
})


def default_output_tokens(kind: str, base: int, *, maximum: int = MAX_MODEL_OUTPUT_TOKENS) -> int:
    """Return the registry default output ceiling for one task kind.

    ``base`` is the host-wide default (``model.max_output_tokens``); the product
    ceiling bounds every result, so a task default can never exceed it. A larger
    explicit host value is kept rather than lowered back to 30000.
    """
    base = int(base) if isinstance(base, int) and not isinstance(base, bool) else 0
    if kind in STRUCTURED_KINDS:
        return max(1, min(maximum, max(base, STRUCTURED_OUTPUT_TOKENS)))
    factor, floor = SHORT_TASK_BUDGETS.get(kind, (1, 0))
    return max(1, min(maximum, max(floor, base * factor)))


def with_thinking_margins(profiles):
    """Apply the table to freshly assembled task profiles.

    Only the registry default carried by each profile changes here; an explicit
    value saved by the user lives in configuration and keeps overriding it.
    """
    return tuple(replace(profile, max_output_tokens=default_output_tokens(profile.kind, profile.max_output_tokens))
                 for profile in profiles)
