"""Review raw writing separately from adopting a generated expression rule."""

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.execution.service import outcome
from companion_v2.foundation.refs import ScopeKind

from .configuration import read_settings


class ExpressionSampleManagement:
    def __init__(self, styles):
        self.styles, self.app = styles, styles.app
        self.runtime, self.samples = styles.runtime, styles.samples

    def _collect(self, context, at):
        policy = read_settings(self.runtime, context)
        samples = self.samples.inspect_inputs(context, at, window_hours=policy.window_hours,
            sample_chars=policy.sample_chars,
            member_ids=policy.selected_members if context.scope.kind is ScopeKind.GROUP else None)
        return policy, samples

    @staticmethod
    def _token(operator, context, value, revision):
        return digest(["expression-sample-decision.v1", operator.subject, operator.binding_id,
            operator.binding_revision, context.key, value, revision])

    @outcome
    def list(self, *, operator, context, state="all", limit=24, after_id=None):
        if state not in ("all", "pending", "approved", "rejected") or type(limit) is not int or not 1 <= limit <= 64:
            raise TaskError("expression_query_invalid", "invalid sample query")
        if after_id is not None and (type(after_id) is not str or len(after_id) > 512):
            raise TaskError("expression_query_invalid", "invalid sample cursor")
        at = self.styles.management.ledger.now()
        with self.runtime.transaction():
            _, revision = self.styles._authorize(operator, context, at)
            policy, samples = self._collect(context, at)
            ordered = sorted(samples.samples, key=lambda sample: (
                stamp(sample.received_at) if sample.received_at else "", sample.handle), reverse=True)
            cursor_index = -1
            if after_id is not None:
                cursor_index = next((index for index, sample in enumerate(ordered) if sample.handle == after_id), -1)
                if cursor_index < 0:
                    raise TaskError("expression_view_stale", "sample cursor is no longer available")
            items, counts, eligible_count = [], {"pending": 0, "approved": 0, "rejected": 0}, 0
            for index, sample in enumerate(ordered):
                value = self.samples.reviews.observe(context, sample, at)
                counts[value["state"]] += 1
                eligible = self.samples.reviews.eligible(context, sample,
                    review_enabled=policy.sample_review_enabled)
                eligible_count += int(eligible)
                if (state != "all" and value["state"] != state) or index <= cursor_index:
                    continue
                actions = ["approve", "reject"] if value["state"] == "pending" else (
                    ["reject"] if value["state"] == "approved" else ["approve"])
                items.append({"handle": sample.handle, "text": sample.text, "sender_id": sample.sender_id,
                    "received_at": stamp(sample.received_at) if sample.received_at else None,
                    "state": value["state"], "decided_at": value["decided_at"], "actions": actions,
                    "eligible": eligible,
                    "view_token": self._token(operator, context, value, revision)})
        return {"items": items[:limit], "counts": counts, "sample_review_enabled": policy.sample_review_enabled,
                "next_cursor": items[limit - 1]["handle"] if len(items) > limit else None,
                "context_key": context.key, "runtime": {**self.app.expression_style.status(context),
                    "eligible_samples": min(eligible_count, policy.sample_limit)}}

    @outcome
    def command(self, *, operator, context, handle, action, view_token, operation_key):
        if action not in ("approve", "reject") or type(handle) is not str or not handle.startswith("input:") or len(handle) > 512:
            raise TaskError("expression_command_invalid", "select an original sample and decision")
        self.styles.management._validate_operation_key(operation_key)
        at = self.styles.management.ledger.now()
        with self.runtime.transaction():
            _, revision = self.styles._authorize(operator, context, at)
            value = self.samples.reviews.read(context, handle)
            if value is None:
                raise TaskError("expression_sample_unavailable", "refresh the sample list")
            decision_key = digest([operator.subject, context.key, operation_key, handle, action])
            state = "approved" if action == "approve" else "rejected"
            if value["decision_key"] == decision_key and value["state"] == state:
                return {"handle": handle, "state": state, "replayed": True}
            if view_token != self._token(operator, context, value, revision):
                raise TaskError("expression_view_stale", "refresh this sample before deciding")
            if action == "approve":
                _, samples = self._collect(context, at)
                sample = next((sample for sample in samples.samples if sample.handle == handle), None)
                if sample is None or (sample.content_hash or digest(sample.text)) != value["content_hash"]:
                    raise TaskError("expression_sample_changed", "original sample changed or is no longer authorized")
            self.samples.reviews.write(context, {**value, "state": state, "revision": value["revision"] + 1,
                "decided_at": stamp(at), "decided_by": operator.subject.subject_id, "decision_key": decision_key})
        self.app.notify()
        return {"handle": handle, "state": state, "replayed": False}

    def _resolve_learn_instruction(self, *, operator, context):
        at = self.styles.management.ledger.now()
        with self.runtime.read_snapshot():
            self.styles._authorize(operator, context, at)
            bound = self.app.state_maintenance.bindings.get(context.key)
            if bound is not None:
                return bound[1]
            row = self.runtime.read_one("""SELECT role_instruction FROM conversation_batches
                WHERE origin_key = ? ORDER BY rowid DESC LIMIT 1""", (context.key,))
            from companion_v2.conversation.native_turns import latest
            row = latest(self.runtime, context.key) or row
            if row is None:
                raise TaskError("expression_sample_unavailable", "this window has no original conversation")
            return row["role_instruction"]

    @outcome
    def prepare_learn(self, *, operator, context):
        """Authorize and prepare manual learning off-loop without creating asyncio tasks."""
        instruction = self._resolve_learn_instruction(operator=operator, context=context)
        immediate, pending = self.app.expression_style.prepare_start(context, instruction)
        return {"result": immediate, "launch": pending}

    @outcome
    def learn(self, *, operator, context):
        instruction = self._resolve_learn_instruction(operator=operator, context=context)
        return self.app.expression_style.start(context, instruction)
