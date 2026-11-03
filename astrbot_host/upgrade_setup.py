"""Review the existing installation before asking an upgrading user to bind again."""

from collections import Counter
from copy import deepcopy

from companion_v2.execution.models import TaskError, digest
from companion_v2.legacy.configuration_migration import (
    convert_account_configuration, convert_plugin_configuration, plugin_configuration_defaults,
)
from .legacy_config import find_v1_data_root, read_v1_host_configuration
from .legacy_persona_mapping import legacy_account_inventory
from .persona_catalog import available_personas


class UpgradeSetup:
    def __init__(self, controller):
        self.controller = controller

    async def plan(self):
        controller = self.controller
        root = getattr(controller, "default_data_root", None) or controller.data_root
        legacy_root, _ = find_v1_data_root(root)
        source, path, fingerprint = read_v1_host_configuration(root)
        from .upgrade_source import is_legacy_configuration
        present = legacy_root.is_dir() and is_legacy_configuration(source)
        current = controller.config.get("multi_host", {})
        configured = bool(current.get("accounts") or current.get("personas") or current.get("targets"))
        if not present or configured:
            return {"present": present, "configured": configured, "can_prepare": False}, None
        names = await available_personas(controller.context)
        accounts = await legacy_account_inventory(
            controller, source, controller.session_topology.accounts(), names)
        mapping = convert_account_configuration(source, accounts=accounts)
        plugin = convert_plugin_configuration(source)
        candidate = deepcopy(dict(controller.config))
        for field, value in mapping.changes.items():
            if field.startswith("multi_host."):
                candidate.setdefault("multi_host", {})[field.split(".", 1)[1]] = deepcopy(value)
        # Only connection prerequisites are prepared here. The normal migration
        # plan still owns the remaining settings and its persisted receipt.
        for field, value in plugin.changes.items():
            if field.startswith(("model.", "runtime.")):
                group, name = field.split(".", 1)
                candidate.setdefault(group, {})[name] = deepcopy(value)
        provider_id = candidate.get("model", {}).get("provider_id", "")
        if not provider_id:
            try:
                provider = controller.context.get_using_provider()
                provider_id = provider.meta().id
                candidate.setdefault("model", {})["provider_id"] = provider_id
            except (AttributeError, TypeError, ValueError):
                pass
        section = candidate.setdefault("multi_host", {})
        from companion_v2.adapters.astrbot.staging_config import REQUIRED_CAPABILITIES
        section.setdefault("capabilities", plugin_configuration_defaults()["multi_host.capabilities"])
        section["capabilities"] = sorted(set(section.get("capabilities", ())) | REQUIRED_CAPABILITIES)
        targets, personas = section.get("targets", []), section.get("personas", [])
        selected = {(row["platform_id"], row["account_id"], row["bot_id"])
                    for row in [*targets, *section.get("group_targets", [])]}
        section["accounts"] = [{field: row[field] for field in ("platform_id", "account_id", "bot_id")}
                               for row in accounts
                               if (row["platform_id"], row["account_id"], row["bot_id"]) in selected]
        issues = [{"field": item.field_id, "reason": item.summary}
                  for item in mapping.differences if item.kind not in ("inactive_source", "disabled_source")]
        ready = bool(section.get("accounts") and (targets or section.get("group_targets")) and personas and provider_id
                     and controller.context.get_provider_by_id(provider_id) is not None)
        section["enabled"] = True
        if ready:
            from .multi_settings import MultiHostSettings
            MultiHostSettings.parse(candidate)
        # Bind the review token to the exact source, native inventory and current
        # destination. A changed account/provider is reviewed again, never guessed.
        token = digest([fingerprint, accounts, candidate])
        persona_bots = Counter(str(row.get("bot_id") or "") for row in personas
                               if isinstance(row, dict) and row.get("bot_id"))
        multi_persona_warning = (
            "检测到同一个 Bot 绑定了多个人格（例如私聊与群聊各一个）。升级前建议先在 AstrBot 侧"
            "合并为单一人格：多个人格映射到分别命名的角色时，同一角色的资料与设置容易被拆成多份，"
            "事后归并很麻烦。" if any(count > 1 for count in persona_bots.values()) else "")
        return {"present": True, "configured": False, "can_prepare": ready,
                "plan_token": token, "source_sha256": fingerprint,
                "source_path": str(path), "v1_root": str(legacy_root),
                "accounts": section.get("accounts", []), "targets": targets,
                "groups": section.get("group_targets", []), "personas": personas,
                "core_persona_id": mapping.changes.get("target.persona_id"),
                "provider_id": provider_id, "issues": issues,
                "multi_persona_warning": multi_persona_warning}, candidate

    async def prepare(self, token):
        document, candidate = await self.plan()
        if candidate is None or not document["can_prepare"]:
            raise TaskError("upgrade_mapping_unavailable", "旧版账号、人格或模型尚未能完整核验，请在账号与会话中确认。")
        if token != document["plan_token"]:
            raise TaskError("legacy_plan_stale", "旧版来源或当前连接已经变化，请重新读取后确认。")
        result = await self.controller.apply_bindings(candidate)
        if result.get("started"):
            # This is an explicit Dashboard confirmation of the existing exact
            # core window, equivalent to the normal binding confirmation.
            targets = candidate["multi_host"].get("targets", [])
            personas = candidate["multi_host"].get("personas", [])
            if len(targets) == 1:
                target = targets[0]
                mapping_persona = document.get("core_persona_id")
                if mapping_persona == "":
                    mapping_persona = "[%None]"
                roles = [row for row in personas if row["bot_id"] == target["bot_id"]
                         and (mapping_persona is None or row["persona_id"] == mapping_persona)]
                if len(roles) == 1:
                    from .binding_store import PendingBinding, write_binding
                    write_binding(self.controller.binding_root, PendingBinding(
                        target["platform_id"], target["account_id"], target["participant_id"],
                        "" if roles[0]["persona_id"] == "[%None]" else roles[0]["persona_id"], True))
                    confirmed = self.controller.confirm_binding()
                    result["binding_confirmed"] = confirmed.get("confirmed", False)
        return result
