"""Inventory configuration addresses without changing installed settings.

Imports the real field factories, resolves the dashboard editors' default write
layer, and records hidden role sharing separately from declared field layers.
Optional database inspection opens SQLite read-only and emits counts and field
names only. Optional behavior probes use an isolated temporary V2 runtime.

This is an audit, not a migration or a pass/fail test of product behavior.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from datetime import datetime, timezone
import importlib
import inspect
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reviewed product scope, distinct from what the current implementation stores.
# Field exceptions below prevent treating a mixed group's entire field count as
# the number of preferences that should be moved onto the role.
GAPS = {
    "conversation.busy_gate": "role_preferences",
    "conversation.group_participation": "role_defaults_with_group_exceptions",
    "conversation.image_review": "role_defaults_with_group_exceptions",
    "conversation.perception": "role_preferences",
    "conversation.presentation": "role_defaults_with_group_exceptions",
    "conversation.recall": "role_policy_with_relationship_privacy",
    "expression.learning": "role_defaults_with_group_exceptions",
    "knowledge": "installation_and_role_defaults",
    "memory": "installation_and_role_defaults",
    "qzone": "role_preferences",
    "role.growth.skills": "role_preferences",
    "role.life": "role_policy_with_scoped_execution_grants",
    "role.state": "role_policy_with_scoped_platform_grants",
    "screen": "administrator_window_privacy_with_explicit_sharing",
}
EXCEPTIONS = {
    "conversation.presentation.group_wakeup_primary_member_ids": "stable_group",
    "expression.learning.group_member_mode": "stable_group",
    "expression.learning.group_member_ids": "stable_group",
    "conversation.recall.transcribe_enabled": "role_policy_and_relationship_privacy",
    "person.learning.acquaintance_learning_enabled": "relationship_privacy",
    "screen.journal_material_enabled": "administrator_window_only_unless_explicitly_shared",
    "screen.interpretation_enabled": "administrator_window_only_plus_exact_device_consent",
    "screen.journal_material_max_chars": "administrator_window_only_by_default",
    "role.life.enabled": "role_policy_and_scoped_execution_grants",
    "role.state.enabled": "role_policy_and_scoped_read_maintain_grants",
    "role.state.typing_enabled": "bot_role_preference_and_scoped_platform_grant",
    "role.state.presence_enabled": "bot_role_preference_and_scoped_platform_grant",
    "role.state.online_status_enabled": "bot_role_preference_and_scoped_platform_grant",
    "role.state.presence_priority": "bot_role_preference_and_scoped_platform_grant",
}


def model_profiles():
    """Use the multi-host assembly, including its conditional modality profiles.

    Parse its declarations instead of importing AstrBot or maintaining a second
    list that could omit profiles present only in multi-host mode.
    """
    from companion_v2.execution.models import Lane
    from companion_v2.model_gateway.models import ModelTaskProfile

    profiles = [ModelTaskProfile("conversation.reply", Lane.FOREGROUND, "scope-audit", "审计")]
    tree = ast.parse((ROOT / "astrbot_host/multi_session.py").read_text(encoding="utf-8"))
    imports = {alias.asname or alias.name: (node.module, alias.name)
               for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module and node.module.startswith("companion_v2.") for alias in node.names}
    assembly = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "profiles" for target in node.targets))
    for call in ast.walk(assembly):
        if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                or not call.func.id.endswith(("_profile", "_profiles"))):
            continue
        module, name = imports[call.func.id]
        options = {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords
                   if isinstance(keyword.value, ast.Constant)}
        result = getattr(importlib.import_module(module), name)("scope-audit", **options)
        profiles.extend(result if isinstance(result, tuple) else (result,))
    if len({profile.kind for profile in profiles}) != len(profiles):
        raise ValueError("model task declarations must be unique")
    return tuple(profiles)


def inventory():
    from companion_v2.configuration.models import ConfigLayer
    from companion_v2.configuration.owners import SparseConfigurationOwner
    from companion_v2.configuration.role_settings import RoleSharedSettingsOwner
    from companion_v2.conversation.segmentation import SegmentationPolicy
    from companion_v2.configuration import management
    from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
    from companion_v2.memory.management import MemoryManagement
    from companion_v2.role_work.state.configuration import DEFAULTS as STATE_DEFAULTS
    from companion_v2.role_work.state.settings_scope import role_preference

    registered, origins, modules = {}, defaultdict(list), {}

    def add(fields, module, factory, line, family="business"):
        for field in fields:
            if field.field_id in registered and registered[field.field_id][0] != field:
                raise ValueError("conflicting field factories: " + field.field_id)
            registered[field.field_id] = (field, family)
            origins[field.field_id].append({"file": module.replace(".", "/") + ".py",
                                           "factory": factory, "line": line})

    for path in sorted((ROOT / "companion_v2").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "configuration_fields" not in source:
            continue
        for node in ast.parse(source).body:
            if not isinstance(node, ast.FunctionDef) or not node.name.endswith("configuration_fields"):
                continue
            name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
            module = importlib.import_module(name)
            modules[name] = module
            factory = getattr(module, node.name)
            required = [p for p in inspect.signature(factory).parameters.values()
                        if p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
            if name == "companion_v2.model_gateway.routing":
                fields = factory(model_profiles())
                family = "model"
            elif name == "companion_v2.conversation.reply_settings":
                fields, family = factory(SegmentationPolicy(), False, 0.5), "business"
            elif required:
                raise ValueError("unhandled required field factory: " + name + ":" + node.name)
            else:
                fields, family = factory(), "business"
            add(fields, name, node.name, node.lineno, family)

    # The host catalog is safe to import and is the source of truth for both
    # bundled extensions and integrations supplied by separate plugins.
    from astrbot_host.integration_catalog import BUNDLED_EXTENSIONS, EXTERNAL_PROVIDERS
    declarations = (*BUNDLED_EXTENSIONS, *EXTERNAL_PROVIDERS)
    from companion_v2.capabilities.configuration import CapabilityConfigurationOwner
    for declaration in declarations:
        extension_id = declaration.get("extension_id")
        if not extension_id:
            continue
        owner = CapabilityConfigurationOwner(None, extension_id=extension_id)
        add(owner.fields, "companion_v2.capabilities.configuration", "CapabilityConfigurationOwner.fields", 32,
            "capability")

    role = SubjectRef("scope-audit", SubjectKind.BOT, "audit-role")
    person = SubjectRef("scope-audit", SubjectKind.HUMAN, "audit-person")
    scope = ScopeRef("scope-audit", "qq", ScopeKind.PRIVATE, "audit-window", "audit-bot",
                     "audit-account", "audit-conversation", "audit-session")
    context = SimpleNamespace(scope=scope, role_binding=SimpleNamespace(role=role),
                              actor=SimpleNamespace(subject=person),
                              audience=SimpleNamespace(scope=scope, recipients=(person,)))
    targets = {}
    for cls in vars(management).values():
        if inspect.isclass(cls) and issubclass(cls, management.GroupEditor) and cls.group:
            editor = cls.__new__(cls)
            editor.context = context
            targets[cls.group] = editor._target().layer.value
    from companion_v2.conversation.perception_management import PerceptionManagement
    targets["conversation.presentation"] = ConfigLayer.ROLE.value
    targets["conversation.perception"] = inspect.signature(PerceptionManagement.__init__).parameters["layer"].default.value
    targets["memory"] = inspect.signature(MemoryManagement.settings).parameters["layer"].default
    field_targets = {"role.state." + name: ConfigLayer.ROLE.value if role_preference(name)
                     else ConfigLayer.WINDOW.value for name in STATE_DEFAULTS}

    owners = {}
    for module in modules.values():
        for cls in vars(module).values():
            if (inspect.isclass(cls) and cls.__module__ == module.__name__
                    and issubclass(cls, SparseConfigurationOwner) and cls.owner_id != "configuration"):
                owners[cls.owner_id] = cls

    groups, fields = {}, []
    for field, family in sorted(registered.values(), key=lambda entry: entry[0].field_id):
        owner_type = owners.get(field.owner_id, SparseConfigurationOwner)
        shared = authoritative = False
        if issubclass(owner_type, RoleSharedSettingsOwner):
            instance = owner_type.__new__(owner_type)
            shared = bool(instance.is_shared(field))
            authoritative = shared and bool(instance.role_value_overrides_window(field))
        gap = GAPS.get(field.group_id)
        if family == "model":
            # The host route selects role defaults unless a page explicitly
            # requests this role's current private window override.
            gap, target = None, "role"
        elif family == "capability":
            gap, target = "preferences_coupled_to_exact_scope", "scope"
        else:
            target = field_targets.get(field.field_id, targets.get(field.group_id))
            if target is None and len(field.layers) == 1:
                target = field.layers[0].value
            if target is None:
                raise ValueError("missing dashboard write target: " + field.group_id)
            # Shared owners can route one field from a window editor to the
            # role default. Report the effective write address, not only the
            # editor's context target.
            if (target == ConfigLayer.WINDOW.value and shared
                    and isinstance(owner_type, type)
                    and issubclass(owner_type, RoleSharedSettingsOwner)
                    and instance.window_write_role_default(field)):
                target = ConfigLayer.ROLE.value
        recommendation = EXCEPTIONS.get(field.field_id)
        if recommendation is None:
            recommendation = ("installation_or_role_default_with_explicit_exception" if family == "model" else
                              "role_preference_separate_from_consent" if family == "capability" else
                              gap or target)
        entry = {"field_id": field.field_id, "group": field.group_id, "family": family,
                 "owner": field.owner_id, "declared_layers": [layer.value for layer in field.layers],
                 "default_write_layer": target, "owner_role_shared": shared,
                 "owner_role_authoritative": authoritative, "finding": gap,
                 "recommended_scope": recommendation, "origins": origins[field.field_id]}
        fields.append(entry)
        group = groups.setdefault(field.group_id, {"family": family, "fields": 0, "finding": gap,
            "default_write_layer": target, "declared_layers": entry["declared_layers"],
            "owner_role_shared_fields": 0, "owner_role_authoritative_fields": 0})
        group["fields"] += 1
        group["owner_role_shared_fields"] += shared
        group["owner_role_authoritative_fields"] += authoritative
        if group["default_write_layer"] != target:
            group["default_write_layer"] = "mixed"

    business_gaps = [g for g in groups.values() if g["family"] == "business" and g["finding"]]
    by_family = {family: {"groups": sum(g["family"] == family for g in groups.values()),
                          "fields": sum(g["fields"] for g in groups.values() if g["family"] == family)}
                 for family in ("business", "model", "capability")}
    return {"generated_at": datetime.now().astimezone().isoformat(), "by_family": by_family,
            "business_groups_with_scope_findings": len(business_gaps),
            "business_fields_in_affected_groups": sum(g["fields"] for g in business_gaps),
            "business_owner_role_shared_fields": sum(g["owner_role_shared_fields"] for g in business_gaps),
            "business_owner_role_authoritative_fields": sum(g["owner_role_authoritative_fields"] for g in business_gaps),
            "optional_model_groups": ["model.conversation.image_narration", "model.conversation.video_narration"],
            "counts_are_group_coverage_not_fields_to_globalize": True,
            "groups": dict(sorted(groups.items())), "fields": fields}


def database_counts(path, fields):
    known = {field["field_id"] for field in fields}
    by_group, by_layer, by_kind = defaultdict(Counter), Counter(), Counter()
    windows, unknown = defaultdict(set), set()
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        rows = connection.execute("""SELECT v.field_id, v.value_json, t.target_json
            FROM configuration_values v JOIN configuration_targets t USING(target_key)""").fetchall()
        for field_id, value, document in rows:
            target = json.loads(document)
            layer = target["layer"]
            group = ("proactive.contact" if field_id.startswith("proactive.contact.") else field_id.rsplit(".", 1)[0])
            by_group[group][layer] += 1
            by_layer[layer] += 1
            if field_id not in known:
                unknown.add(field_id)
            if layer == "window":
                native = target.get("scope")
                kind = native.get("kind") if isinstance(native, dict) else native[1]
                by_kind[kind] += 1
                windows[(target.get("role_id"), target.get("bot_id"), field_id)].add(value)
        conflicts = [key for key, values in windows.items() if len(values) > 1]
        capability_rows = connection.execute("SELECT COUNT(*) FROM capability_settings").fetchone()[0]
        return {"configuration_value_rows": len(rows), "by_layer": dict(sorted(by_layer.items())),
                "window_rows_by_kind": dict(sorted(by_kind.items())),
                "by_group": {key: dict(sorted(value.items())) for key, value in sorted(by_group.items())},
                "conflicting_role_field_pairs": len(conflicts),
                "conflicting_field_ids": sorted({key[2] for key in conflicts}),
                "stored_field_ids_outside_current_catalog": sorted(unknown),
                "capability_settings_rows": capability_rows}
    finally:
        connection.close()


def behavior_probes():
    from companion_v2.configuration.models import ConfigLayer, ConfigTarget, ConfigurationError
    from companion_v2.configuration.owners import SparseConfigurationOwner
    from companion_v2.conversation.segmentation import SegmentationPolicy
    from companion_v2.conversation.reply_settings import (GROUP, configuration_fields,
        ReplyConfigurationOwner)
    from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
    from companion_v2.qzone.configuration import configuration_fields as qzone_fields, QzoneConfigurationOwner
    from companion_v2.role_work.state.configuration import configuration_fields as state_fields, StateConfigurationOwner
    from companion_v2.role_work.growth.configuration import SKILL_FIELDS, SkillConfigurationOwner
    from companion_v2.storage.runtime import StorageRuntime
    from companion_v2.model_gateway.routing import ModelRouter, configuration_fields as model_fields

    result = {}
    with TemporaryDirectory(prefix="v2-scope-audit-") as temporary:
        with StorageRuntime.open(data_root=temporary, installation_id="scope-audit") as runtime:
            role = SubjectRef("scope-audit", SubjectKind.BOT, "audit-role")
            def window(kind, name):
                return ConfigTarget("scope-audit", ConfigLayer.WINDOW, role,
                    ScopeRef("scope-audit", "qq", kind, name, "audit-bot", "audit-account",
                             "conversation-" + name, "session-" + name))
            private, group = window(ScopeKind.PRIVATE, "private"), window(ScopeKind.GROUP, "group")
            role_target = ConfigTarget("scope-audit", ConfigLayer.ROLE, role, bot_id="audit-bot")
            sparse = SparseConfigurationOwner(runtime)
            def seed(target, definitions, changes):
                sparse.write(target, definitions, changes=changes, reset=(),
                    expected_revision=sparse.read(target, definitions).revision,
                    observed_at=datetime.now(timezone.utc))
            definitions = configuration_fields(SegmentationPolicy(), False, 0.5)
            key = GROUP + ".group_wakeup_direct_words"
            owner = ReplyConfigurationOwner(runtime)
            role_snapshot = owner.read(role_target, definitions)
            owner.write(role_target, definitions, changes={key: '["角色默认词"]'}, reset=(),
                expected_revision=role_snapshot.revision, observed_at=datetime.now(timezone.utc))
            group_snapshot = owner.read(group, definitions)
            owner.write(group, definitions, changes={key: '["群聊覆盖词"]'}, reset=(),
                expected_revision=group_snapshot.revision, observed_at=datetime.now(timezone.utc))
            replaced_group = ConfigTarget("scope-audit", ConfigLayer.WINDOW, role,
                ScopeRef("scope-audit", "qq", ScopeKind.GROUP, "group", "audit-bot", "audit-account",
                         "replacement-conversation", "session-group"))
            other_group = window(ScopeKind.GROUP, "other-group")
            result["group_override_stays_local_and_survives_reconnection"] = {
                "role_default": owner.read(role_target, definitions).fields[key].value,
                "selected_group": owner.read(group, definitions).fields[key].value,
                "reconnected_group": owner.read(replaced_group, definitions).fields[key].value,
                "other_group": owner.read(other_group, definitions).fields[key].value}
            second_private = window(ScopeKind.PRIVATE, "private-second")
            for name, factory, owner_type, key in (
                ("qzone_role_inheritance_and_window_override", qzone_fields, QzoneConfigurationOwner,
                 "qzone.enabled"),
                ("state_role_inheritance_and_window_override", state_fields, StateConfigurationOwner,
                 "role.state.hunger_enabled"),
            ):
                definitions = factory()
                seed(role_target, definitions, {key: False})
                seed(private, definitions, {key: True})
                owner = owner_type(runtime)
                result[name] = {"saved_role_value": False,
                                "inherited_window_value": owner.read(second_private, definitions).fields[key].value,
                                "explicit_window_override": owner.read(private, definitions).fields[key].value}
            inherited = QzoneConfigurationOwner(runtime).read(group, qzone_fields()).fields["qzone.enabled"]
            result["role_provenance_marks_window_as_explicit"] = {
                "source_layer": inherited.source.layer.value,
                "edited_layer": group.layer.value, "reported_explicit": inherited.explicit}
            skills = SkillConfigurationOwner(runtime)
            try:
                skills.write(role_target, SKILL_FIELDS, changes={"role.growth.skills.custom_skills": '["审计"]'},
                    reset=(), expected_revision=skills.read(role_target, SKILL_FIELDS).revision,
                    observed_at=datetime.now(timezone.utc))
                result["direct_skill_role_write"] = "succeeded"
            except ConfigurationError as error:
                result["direct_skill_role_write"] = error.code
            profile = model_profiles()[0]
            definitions = model_fields((profile,))
            key = profile.group + ".provider"
            seed(role_target, definitions, {key: "role-model"})
            snapshot = sparse.read(private, definitions)
            router = ModelRouter.__new__(ModelRouter)
            router.default_provider_resolver = lambda context, profile: "host-model"
            router.providers = {"host-model": None, "role-model": None, "scope-audit": None}
            host_provider = router.provider_default(None, profile, snapshot)
            routed = router.apply_provider_default(profile, snapshot, host_provider)
            result["host_default_overwrites_saved_role_provider"] = {
                "inherited_saved_value": snapshot.fields[key].value,
                "inherited_source": snapshot.fields[key].source.layer.value,
                "routed_value": routed.fields[key].value}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="save the field inventory as UTF-8 JSON")
    parser.add_argument("--database", type=Path, help="inspect one existing V2 database read-only")
    parser.add_argument("--probes", action="store_true", help="reproduce storage behavior in a temporary runtime")
    args = parser.parse_args()
    result = inventory()
    if args.database:
        result["database"] = database_counts(args.database, result["fields"])
    if args.probes:
        result["behavior_probes"] = behavior_probes()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    summary = {key: value for key, value in result.items() if key not in ("groups", "fields", "database")}
    if "database" in result:
        summary["database"] = {key: value for key, value in result["database"].items() if key != "by_group"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
