"""Which stored expression rules one conversation window can actually inject.

A rule that can never be injected in the current window must not consume that
window's active-rule budget, block a restore, or hide a learnable rule from the
learning gate. The review page, the review commands and expression learning all
ask this module, so the capacity they report and the rules reply injection can
really use are one set.

The predicate mirrors every precondition ``conversation.style_context`` checks
before it offers a stored rule to the per-turn rotation: the stored window must
still be the window this turn belongs to, the captured source must still resolve
for this conversation and role, and the approval that admitted the samples must
not have been revoked. The final per-message re-read (``style_context._material``)
stays where it is: it re-reads conversation text, so a budget count must not
depend on it, and it only ever drops a rule the turn is already projecting.

A self-contained rule (``shared_pack``/``legacy_expression``) carries its own
text, so it follows its scope family through AstrBot's conversation rotations.
A dialogue rule (``conversation_input``/``confirmed_reply``) is re-read from the
live window it was captured in: in a group it stays bound to that exact window,
while a private participant's windows share one family and re-authorize the
source by participant and role.
"""

from __future__ import annotations

import json

from companion_v2.execution.models import digest
from companion_v2.foundation.refs import ScopeKind


NAMED_SOURCES = ("shared_pack", "legacy_expression")

# Why a listed rule is outside the set reply injection can use. They stay
# separate because they mean different things to the operator: the window moved
# on, the evidence itself cannot be re-read, or the samples it cites were
# withdrawn after they had been approved.
WINDOW_ROTATED = "expression_rule_window_rotated"
SAMPLE_CHANGED = "expression_sample_changed"
REVIEW_REVOKED = "expression_rule_review_revoked"


def source_kind(window):
    """The source kind stored in one run window, or ``None`` when unreadable."""
    if type(window) is not dict:
        return None
    value = window.get("source_kind", "confirmed_reply")
    return value if type(value) is str else None


def window_rotated(context, scope_key, source_kind):
    """Whether the rule is bound to a conversation window this turn has left.

    This is a lifecycle event, not unreadable evidence: the rule and its stored
    window are intact, the current turn simply belongs to another window. A group
    dialogue rule is only reread from its own window. Private self-contained
    rules follow the participant through rotations, while private dialogue rules
    re-authorize their original source in the new window.
    """
    if source_kind is None or scope_key == digest(context.scope):
        return False
    return context.scope.kind is ScopeKind.GROUP and source_kind not in NAMED_SOURCES


def rule_reason(context, scope_key, window, handles, *, samples=None, at=None):
    """Why reply injection would skip this stored rule here, or ``None``.

    ``samples`` is the source catalogue: it resolves the window the samples were
    captured in, re-authorizes a private source, and reads the sample approvals.
    Without it only the window itself can be judged, which is what the cheap
    candidate scans need.
    """
    kind = source_kind(window)
    if kind is None:
        return SAMPLE_CHANGED
    if window_rotated(context, scope_key, kind):
        return WINDOW_ROTATED
    if kind in NAMED_SOURCES or samples is None:
        # A self-contained rule carries its own text and re-reads nothing.
        return None
    if type(handles) is not list or any(type(value) is not str for value in handles):
        return SAMPLE_CHANGED
    try:
        source = samples.evidence_context(window, context)
    except Exception:
        # The window the samples were captured in no longer resolves for this
        # conversation, so the rule cannot be re-read from it.
        return SAMPLE_CHANGED
    if at is not None and context.scope.kind is ScopeKind.PRIVATE:
        try:
            if not samples.private_source_available(source, context, at):
                return SAMPLE_CHANGED
        except Exception:
            return SAMPLE_CHANGED
    if kind == "conversation_input":
        # ``allows_handles`` is the approval gate injection applies last: a
        # rejected or withdrawn sample ends the rule's influence even though the
        # rule row itself is intact.
        try:
            if not samples.reviews.allows_handles(source, handles):
                return REVIEW_REVOKED
        except Exception:
            return SAMPLE_CHANGED
    return None


def row_usable(context, row, *, samples=None, at=None):
    """The same judgement for a joined ``expression_style_runs``/signals row."""
    try:
        window = json.loads(row["sample_json"])
        handles = json.loads(row["evidence_json"])
    except (TypeError, ValueError):
        return False
    return rule_reason(context, row["scope_key"], window, handles,
                       samples=samples, at=at) is None


def rule_scope_counts(runtime, context, owner_key, family_keys, *, samples=None,
                      at=None, connection=None):
    """Return injectable pending/adopted counts by state and rotated row count.

    ``rotated`` is the number of stored rows that are intact but belong to an
    earlier window. It is deliberately separate from rows whose evidence cannot
    be re-read or whose samples were withdrawn: the page may say how many rules
    are window-bound without calling them broken.
    """
    keys = tuple(family_keys)
    if not keys:
        return {}, 0
    placeholders = ",".join("?" for _ in keys)
    statement = f"""SELECT s.state, s.scope_key, s.evidence_json, r.sample_json
        FROM expression_style_signals s JOIN expression_style_runs r ON r.run_id = s.run_id
        WHERE s.owner_key = ? AND s.scope_key IN ({placeholders})
          AND s.state IN ('pending', 'adopted')"""
    parameters = (owner_key, *keys)
    rows = (connection.execute(statement, parameters).fetchall()
            if connection is not None else runtime.read_all(statement, parameters))
    counts, rotated = {}, 0
    for row in rows:
        try:
            window = json.loads(row["sample_json"])
            handles = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            continue
        kind = source_kind(window)
        if kind is None:
            continue
        if rule_reason(context, row["scope_key"], window, handles,
                       samples=samples, at=at) is None:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        elif window_rotated(context, row["scope_key"], kind):
            rotated += 1
    return counts, rotated


def usable_rule_counts(runtime, context, owner_key, family_keys, *, samples=None,
                       at=None, connection=None):
    """Pending and adopted rules this window can inject, by state."""
    return rule_scope_counts(runtime, context, owner_key, family_keys, samples=samples,
                             at=at, connection=connection)[0]


def active_rule_count(runtime, context, owner_key, family_keys, *, samples=None,
                      at=None, connection=None):
    """Pending or adopted rules of this family that this window can inject."""
    return sum(usable_rule_counts(runtime, context, owner_key, family_keys, samples=samples,
                                  at=at, connection=connection).values())
