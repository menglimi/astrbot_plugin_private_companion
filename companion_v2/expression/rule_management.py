"""Edit a rule while preserving its original source and review state."""

from companion_v2.execution.models import TaskError, digest, encode, stamp
from companion_v2.execution.service import outcome

from . import rule_details
from .learning import DIMENSIONS, MAX_PROPOSAL_CHARS, form_identity, normalize_applicability


@outcome
def revise(styles, *, operator, context, signal_id, view_token, operation_key, changes):
    styles.management._validate_operation_key(operation_key)
    if type(changes) is not dict or set(changes) != {"dimension", "expression", "proposal", "applicability"}:
        raise TaskError("expression_revision_invalid", "supply a complete expression revision")
    try:
        form = rule_details.normalize(changes["expression"])
        applicability = normalize_applicability(changes["applicability"])
        proposal = changes["proposal"]
        if (changes["dimension"] not in DIMENSIONS or type(proposal) is not str or not proposal.strip()
                or len(proposal) > MAX_PROPOSAL_CHARS):
            raise ValueError
    except (ValueError, TypeError):
        raise TaskError("expression_revision_invalid", "check the situation, pattern and usage") from None
    at = styles.management.ledger.now()
    with styles.runtime.transaction() as connection:
        owner, revision = styles._authorize(operator, context, at)
        row = styles._row(signal_id, owner, context)
        decision = digest(["expression-revise.v1", operator.subject, context.key, operation_key, signal_id, changes])
        if row["decision_key"] == decision:
            return {"signal_id": signal_id, "state": row["state"], "replayed": True}
        if view_token != styles._token(operator, context, row, revision):
            raise TaskError("expression_view_stale", "refresh the rule before revising")
        styles._basis(row, owner, context, at)
        identity = form_identity(form)
        if identity is not None:
            # A group owns its rules across conversation windows, so the same
            # situation and pattern is still one rule there.
            keys = styles._family(context)
            placeholders = ",".join("?" * len(keys))
            others = styles.runtime.read_all(f"""SELECT signal_id FROM expression_style_signals
                WHERE owner_key = ? AND scope_key IN ({placeholders}) AND signal_id != ?
                  AND state IN ('pending', 'adopted')""", (digest(owner), *keys, signal_id))
            if any(form_identity(rule_details.form_for(styles.runtime, other)) == identity for other in others):
                raise TaskError("expression_duplicate_rule",
                                "another active rule already uses this situation and pattern")
        # observation is the learned note and expression.situation is when the
        # form applies; an edit carries the form, so the original observation
        # stays as evidence read from the source rather than being overwritten.
        connection.execute("""UPDATE expression_style_signals SET dimension = ?, proposal = ?,
            applicability_json = ?, decided_at = ?, decided_by = ?, decision_key = ? WHERE signal_id = ?""",
            (changes["dimension"], proposal.strip(), encode(applicability), stamp(at),
             operator.subject.subject_id, decision, signal_id))
        rule_details.write(styles.runtime, signal_id, form, at=at, actor=operator.subject.subject_id,
                           operation_key=decision)
    styles.app.notify()
    return {"signal_id": signal_id, "state": row["state"], "replayed": False}
