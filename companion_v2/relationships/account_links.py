"""Explicit, revocable private-account associations; never rewrite native routes.

One bounded control-plane document uses the existing runtime_metadata store.
All mutations use a transaction, global CAS revision and stable operation receipt.
No identity, grant, persona mapping or original conversation row is changed.
"""
from __future__ import annotations

from copy import deepcopy
import json

from companion_v2.execution.models import TaskError, digest, stamp
from companion_v2.foundation.refs import ScopeKind, SubjectKind

KEY = "relationships.private-account-links.v1"
MAX_GROUPS = 64
MAX_MEMBERS = 16


def member_for(context):
    scope = context.scope
    if (scope.kind is not ScopeKind.PRIVATE
            or context.actor.subject.kind is not SubjectKind.HUMAN
            or context.audience.recipients != (context.actor.subject,)
            or scope.native_scope_id == "*"):
        raise TaskError("account_link_private_required", "select one concrete private account")
    return {"subject_id": context.actor.subject.subject_id,
            "platform": scope.platform, "bot_id": scope.bot_id,
            "account_id": scope.account_id, "native_account_id": scope.native_scope_id,
            "role_id": context.role_binding.role.subject_id}


def member_key(member):
    return digest(member)


class PrivateAccountLinks:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.configuration_store

    def read(self):
        row = self.store.read_one("SELECT value FROM runtime_metadata WHERE key = ?", (KEY,))
        return json.loads(row["value"]) if row else {"revision": 0, "groups": [], "receipts": {}}

    def group_for(self, context):
        member = member_for(context)
        return next((group for group in self.read()["groups"] if member in group["members"]), None)

    def status(self, context):
        state = self.read()
        member = member_for(context)
        group = next((group for group in state["groups"] if member in group["members"]), None)
        return {"revision": state["revision"], "group": deepcopy(group)}

    def configure(self, context, contexts, *, persona_id, expected_revision,
                  share_context, operation_key, operator_id, at, sharing=None, seed=None, initial_values=None,
                  identity=None, proactive=None, memory=None, command_hash=None):
        # The host resolves current identities/personas; no client-supplied scope
        # or subject is trusted to construct a new identity here.
        if (type(share_context) is not bool or type(persona_id) is not str or not persona_id
                or type(operation_key) is not str or not 1 <= len(operation_key) <= 100
                or type(expected_revision) is not int
                or not 2 <= len(contexts) <= MAX_MEMBERS):
            raise TaskError("account_link_command_invalid", "provide bounded accounts and explicit sharing consent")
        members = [member_for(item) for item in contexts]
        selected = member_for(context)
        if (selected not in members or len({member_key(item) for item in members}) != len(members)
                or len({item["role_id"] for item in members}) != 1
                or any(item.scope.installation_id != self.runtime.installation_id for item in contexts)):
            raise TaskError("account_link_persona_mismatch", "link distinct private accounts of one persona only")
        members.sort(key=member_key)
        payload = {"members": members, "persona_id": persona_id, "share_context": share_context}
        if memory is not None:
            from .account_link_memory import validate_policy
            validate_policy(memory)
            payload["memory"] = memory
        if sharing is not None:
            from .account_link_settings import validate_sharing
            validate_sharing(sharing, initial_values or {})
            if seed not in members:
                raise TaskError("account_link_seed_required", "select an associated account to initialize new shared values")
            from .profile import target_for as profile_target
            from companion_v2.conversation.reply_settings import target_for as reply_target
            from companion_v2.proactive.configuration import target_for as contact_target
            targets = {"relationship.profile": [profile_target(c).key for c in contexts],
                       "conversation.presentation": [reply_target(c).key for c in contexts],
                       "proactive.contact": [contact_target(c).key for c in contexts]}
            payload.update(sharing=sharing, seed=seed, initial_values=initial_values or {}, configuration_targets=targets)
        if identity is not None:
            from .account_link_identity import validate_identity
            if type(identity) is not dict or type(identity.get("enabled")) is not bool:
                raise TaskError("account_link_identity_invalid", "identity sharing requires a separate explicit switch")
            if identity["enabled"]:
                validate_identity(identity)
            payload["identity"] = identity
        if proactive is not None:
            if (type(proactive) is not dict or not {"mode", "preferred"} <= set(proactive)
                    or set(proactive) - {"mode", "preferred", "weights"}
                    or proactive["mode"] not in ("off", "specified", "random")
                    or (proactive["mode"] == "specified" and proactive["preferred"] not in members)
                    or (proactive["mode"] != "specified" and proactive["preferred"] is not None)):
                raise TaskError("account_link_proactive_invalid", "select off, one specified member, or random eligible account")
            proactive = deepcopy(proactive)
            if "weights" in proactive:
                weights = proactive["weights"]
                if (proactive["mode"] != "random" or type(weights) is not list
                        or len(weights) != len(members)
                        or any(type(entry) is not dict or set(entry) != {"member", "weight"}
                               or entry["member"] not in members or type(entry["weight"]) is not int
                               or not 0 <= entry["weight"] <= 1000 for entry in weights)
                        or len({member_key(entry["member"]) for entry in weights}) != len(members)
                        or not any(entry["weight"] > 0 for entry in weights)):
                    raise TaskError("account_link_weights_invalid",
                        "provide each associated account once with integer weight 0..1000, at least one positive")
                proactive["weights"] = sorted(weights, key=lambda entry: member_key(entry["member"]))
            payload["proactive"] = proactive
        return self._mutate(selected, payload, expected_revision, operation_key, operator_id, at, command_hash=command_hash)

    def unlink(self, context, *, expected_revision, operation_key, operator_id, at):
        if (type(expected_revision) is not int or type(operation_key) is not str
                or not 1 <= len(operation_key) <= 100):
            raise TaskError("account_link_command_invalid", "provide a revision and stable operation key")
        return self._mutate(member_for(context), None, expected_revision, operation_key, operator_id, at)

    def _mutate(self, selected, payload, revision, operation_key, operator_id, at, *, command_hash=None):
        operation = digest([operator_id, operation_key])
        # Seed values are resolved server-side and may change between identical HTTP
        # retries. Idempotency binds to the operator's seed selection, not a reread.
        command = {k: v for k, v in payload.items() if k != "initial_values"} if payload else None
        parameter_hash = command_hash or digest([selected, command, revision])
        with self.store.transaction() as connection:
            state = self.read()
            receipt = state["receipts"].get(operation)
            if receipt is not None:
                if receipt["hash"] != parameter_hash:
                    raise TaskError("account_link_operation_conflict", "operation key already has different parameters")
                return {**self.status_from(state, selected), "replayed": True}
            if revision != state["revision"]:
                raise TaskError("account_link_revision_conflict", "read the association again before saving")
            previous = next((g for g in state["groups"] if selected in g["members"]), None)
            if payload is not None:
                claimed = {member_key(m) for g in state["groups"] if g != previous for m in g["members"]}
                if any(member_key(m) in claimed for m in payload["members"]):
                    raise TaskError("account_link_already_linked", "unlink the other account group first")
                if previous is None and len(state["groups"]) >= MAX_GROUPS:
                    raise TaskError("account_link_limit", "too many account associations")
            state["groups"] = [g for g in state["groups"] if g != previous]
            if payload is not None:
                unchanged_epoch = (previous is not None and previous["members"] == payload["members"]
                    and previous["persona_id"] == payload["persona_id"]
                    and previous["share_context"] is True and payload["share_context"] is True)
                group = {**payload, "id": previous["id"] if previous else "accounts-" + digest([operation, selected])[:24],
                         "context_since": previous["context_since"] if unchanged_epoch else stamp(at)}
                for key in ("sharing", "shared_values", "shared_revision", "identity", "proactive", "configuration_targets"):
                    if key not in group and previous and key in previous:
                        group[key] = deepcopy(previous[key])
                from .account_link_memory import apply_policy
                apply_policy(group, previous, at)
                initial = group.pop("initial_values", None)
                if initial is not None:
                    active = {key for keys in group["sharing"].values() for key in keys}
                    old_active = {key for keys in (previous or {}).get("sharing", {}).values() for key in keys}
                    old_values = (previous or {}).get("shared_values", {})
                    group["shared_values"] = {key: old_values[key] if key in old_active and key in old_values
                                               else initial[key] for key in sorted(active)}
                    group["shared_revision"] = (previous or {}).get("shared_revision", 0) + 1
                state["groups"].append(group)
            state["revision"] += 1
            state["receipts"][operation] = {"hash": parameter_hash}
            # Bounded replay horizon. A pruned stale operation still fails CAS;
            # it cannot recreate a removed association.
            while len(state["receipts"]) > 128:
                del state["receipts"][next(iter(state["receipts"]))]
            connection.execute("INSERT OR REPLACE INTO runtime_metadata VALUES (?, ?)",
                (KEY, json.dumps(state, ensure_ascii=False, separators=(",", ":"))))
            return {**self.status_from(state, selected), "replayed": False}

    @staticmethod
    def status_from(state, selected):
        return {"revision": state["revision"], "group": deepcopy(next(
            (g for g in state["groups"] if selected in g["members"]), None))}
