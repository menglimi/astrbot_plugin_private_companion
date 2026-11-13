"""Explicit account, participant and persona mappings for one shared installation."""

from collections import Counter
from dataclasses import asdict, dataclass, field, replace

from companion_v2.adapters.astrbot.native import HostAccount, route_id
from companion_v2.adapters.astrbot.staging_config import ALLOWED_CAPABILITIES, REQUIRED_CAPABILITIES
from companion_v2.execution.models import digest
from companion_v2.conversation.forwards.models import ForwardIntakePolicy
from companion_v2.conversation.segmentation import SegmentationPolicy
from .identity_derivation import role_display_name
from .settings import (HostControlError, HostSettings, PROFILE_PATTERN, _boolean, _section, _text,
                       repair_host_policy_sections)
from .group_settings import GroupTarget, parse_group_targets


@dataclass(frozen=True)
class PrivateTarget:
    target_id: str
    platform_id: str
    account_id: str
    bot_id: str
    participant_id: str

    @property
    def route_key(self):
        return self.platform_id, self.account_id, self.participant_id


@dataclass(frozen=True)
class PersonaMapping:
    bot_id: str
    persona_id: str
    role_id: str


def find_persona_mapping(mappings, bot_id, persona_id):
    """Resolve a persona mapping, including a lone legacy follow-window entry."""
    def value(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

    rows = [row for row in mappings if value(row, "bot_id") == bot_id
            and value(row, "persona_id") == persona_id]
    if rows:
        return rows[0]
    bot_rows = [row for row in mappings if value(row, "bot_id") == bot_id]
    if len(bot_rows) == 1 and value(bot_rows[0], "persona_id") == "[%None]":
        if isinstance(bot_rows[0], dict):
            return {**bot_rows[0], "persona_id": persona_id}
        return replace(bot_rows[0], persona_id=persona_id)
    return None


@dataclass(frozen=True)
class WindowLink:
    """One primary private window and the windows merged into it.

    ``mode='role'`` shares the role/memory but keeps each native window for
    replies.  ``mode='primary'`` additionally routes replies and proactive
    contacts to the primary window.
    """

    primary_target_id: str
    alias_target_ids: tuple[str, ...]
    mode: str = "primary"


def _shared_persona_roles(personas: tuple[PersonaMapping, ...]) -> tuple[PersonaMapping, ...]:
    """Keep one role id when the same AstrBot persona uses the same character name.

    AstrBot personas are global while V2 roles used to be account-scoped.  When
    the same persona is mapped under several Bot accounts with the same visible
    role name, that is one character that should keep one memory owner, not two
    roles.  Differently named roles stay separate so an explicit split remains
    possible.
    """
    by_persona = {}
    role_counts = Counter(mapping.role_id for mapping in personas)
    for mapping in personas:
        by_persona.setdefault(mapping.persona_id, []).append(mapping)
    canonical = {}
    for persona_id, rows in by_persona.items():
        role_ids = {row.role_id for row in rows}
        if len(role_ids) <= 1:
            continue
        labels = {role_display_name(row.role_id, [persona_id]) for row in rows}
        if len(labels) != 1:
            continue

        def rank(row):
            explicit = not row.role_id.startswith(("role-", "staging-"))
            return (role_counts[row.role_id], explicit, row.role_id)

        canonical[persona_id] = max(rows, key=rank).role_id
    if not canonical:
        return personas
    return tuple(replace(mapping, role_id=canonical.get(mapping.persona_id, mapping.role_id))
                 for mapping in personas)


def _parse_window_links(values, targets):
    if values is None:
        return ()
    if type(values) is not list or len(values) > 64:
        raise HostControlError("host_mappings_invalid", "window links must be a bounded list")
    private = {target.target_id: target for target in targets}
    links, claimed = [], set()
    for row in values:
        if (type(row) is not dict or set(row) - {"primary_target_id", "alias_target_ids", "mode"}
                or not {"primary_target_id", "alias_target_ids"}.issubset(row)):
            raise HostControlError("host_mappings_invalid", "window link fields are incomplete or unknown")
        primary = row["primary_target_id"]
        aliases, mode = row["alias_target_ids"], row.get("mode", "primary")
        if (type(primary) is not str or primary not in private or type(aliases) is not list
                or not aliases or len(aliases) > 128 or mode not in ("primary", "role")
                or private[primary].participant_id == "*"):
            raise HostControlError("host_mappings_conflict", "window links need private targets and a valid mode")
        kept = []
        for alias in aliases:
            if (type(alias) is not str or alias not in private or alias == primary
                    or private[alias].participant_id == "*" or alias in claimed or alias in kept):
                raise HostControlError("host_mappings_conflict", "a private window can only be merged once")
            kept.append(alias)
            claimed.add(alias)
        links.append(WindowLink(primary, tuple(kept), mode))
    return tuple(sorted(links, key=lambda item: item.primary_target_id))


@dataclass(frozen=True)
class MultiHostSettings:
    enabled: bool
    installation_key: str
    targets: tuple[PrivateTarget, ...]
    personas: tuple[PersonaMapping, ...]
    account_bindings: tuple[HostAccount, ...]
    capabilities: tuple[str, ...]
    common: HostSettings
    group_targets: tuple[GroupTarget, ...] = ()
    group_delivery_mode: str = "onebot_group"
    window_links: tuple[WindowLink, ...] = ()
    _role_aliases: tuple[tuple[str, str], ...] = field(default=(), compare=False, repr=False)
    _legacy_group_delivery_shape: bool = field(default=False, compare=False, repr=False)
    _storage_installation_id: str | None = field(default=None, compare=False, repr=False)

    @property
    def all_targets(self):
        return self.targets + self.group_targets

    @property
    def role_aliases(self):
        return dict(self._role_aliases)

    def canonical_role_id(self, role_id):
        """Follow role ids that were merged into a shared AstrBot persona role."""
        value = str(role_id or "")
        aliases = self.role_aliases
        seen = set()
        while value in aliases and value not in seen:
            seen.add(value)
            value = aliases[value]
        return value

    @property
    def alias_target_ids(self):
        return frozenset(alias for link in self.window_links for alias in link.alias_target_ids)

    @property
    def primary_targets(self):
        """Private windows that may own proactive work and core binding."""
        aliases = {alias for link in self.window_links if link.mode == "primary"
                   for alias in link.alias_target_ids}
        return tuple(target for target in self.targets if target.target_id not in aliases)

    @property
    def installation_id(self):
        return self._storage_installation_id or "astrbot-private-companion-v2:multi:" + self.installation_key

    @property
    def profile_id(self):
        return self.installation_key

    @property
    def accounts(self):
        return self.account_bindings

    @property
    def fingerprint(self):
        data = asdict(self)
        if not self.common.streaming:
            data["common"].pop("streaming")
        if self.common.segmentation == SegmentationPolicy():
            data["common"].pop("segmentation")
        if self.common.forward_intake == ForwardIntakePolicy():
            data["common"].pop("forward_intake")
        elif self.common.forward_intake.parse_nested:
            # The default predates this field. Preserve custom intake fingerprints
            # written before the switch existed; an explicit False is serialized.
            data["common"]["forward_intake"].pop("parse_nested")
        if not self.common.budget_soft.enabled:
            data["common"].pop("budget_soft")  # A disabled daily soft ceiling is not an identity pin.
        data["common"].pop("voice_reply_enabled")  # Delivery presentation can change without re-pinning identity.
        data["common"].pop("voice_reply_policy")
        # Routing is adopted at assembly/request boundaries, not an identity pin.
        for field in ("additional_providers", "fallback_provider_id", "replacement_rules", "fallback_on_refusal"):
            data["common"].pop(field)
        if not self.group_targets:
            data.pop("group_targets")  # Preserve already initialized private-only installations.
        data.pop("_role_aliases")
        data.pop("_legacy_group_delivery_shape")
        data.pop("_storage_installation_id")
        if self._legacy_group_delivery_shape:
            data.pop("group_delivery_mode")  # Reproduce manifests pinned before group delivery was fixed.
        return digest(data)

    @property
    def binding_contract(self):
        """Stable routing identity, separate from model and runtime settings."""
        return {
            "accounts": sorted(
                ({"platform_id": item.platform_id, "account_id": item.self_id, "bot_id": item.bot_id}
                 for item in self.accounts),
                key=lambda item: (item["platform_id"], item["account_id"], item["bot_id"])),
            "private_targets": sorted(
                (asdict(item) for item in self.targets),
                key=lambda item: item["target_id"]),
            "group_targets": sorted(
                (asdict(item) for item in self.group_targets),
                key=lambda item: item["target_id"]),
            "personas": sorted(
                (asdict(item) for item in self.personas),
                key=lambda item: (item["bot_id"], item["persona_id"])),
        }

    def delivery_mode_for(self, target):
        return "onebot_group" if isinstance(target, GroupTarget) else "onebot_private"

    @classmethod
    def parse(cls, config):
        section = _section(config, "multi_host")
        enabled = _boolean(section.get("enabled", False))
        key = _text(section.get("installation_id", "main"), required=True)
        if not PROFILE_PATTERN.fullmatch(key):
            raise HostControlError("host_installation_invalid", "installation ID must be a stable lowercase identifier")
        base = {"model": _section(config, "model"), "runtime": _section(config, "runtime"),
                "segmentation": config.get("segmentation", {}) if hasattr(config, "get") else {},
                "voice_reply": config.get("voice_reply", {}) if hasattr(config, "get") else {},
                "staging": {"enabled": enabled, "profile_id": "multi-" + key,
                            "delivery_mode": section.get("delivery_mode", "onebot_private")}}
        # Account/role/window mapping does not own optional reply style
        # sections.  Normalize stale policy values for this parse so a mapping
        # save cannot be blocked by an unrelated legacy segmentation/voice
        # field; the save path persists the same repair.
        repair_host_policy_sections(base)
        # The shared model/runtime profile contains no per-target identity.
        base["staging"]["profile_id"] = key if key != "preview-1" else "multi-preview"
        common = HostSettings.parse(base)
        targets, personas = [], []
        fields = ("target_id", "platform_id", "account_id", "bot_id", "participant_id")
        for name, names, kind, result in (("targets", fields, PrivateTarget, targets),
                ("personas", ("bot_id", "persona_id", "role_id"), PersonaMapping, personas)):
            values = section.get(name, [])
            if type(values) is not list:
                raise HostControlError("host_mappings_invalid", "host mappings must be lists")
            for value in values:
                if type(value) is not dict or set(value) - set(names) - {"__template_key"} or set(names) - set(value):
                    raise HostControlError("host_mappings_invalid", "host mapping fields are incomplete or unknown")
                result.append(kind(**{field: _text(value[field], required=True) for field in names}))
        groups = parse_group_targets(section.get("group_targets", []))
        raw_accounts = section.get("accounts")
        if raw_accounts is not None and type(raw_accounts) is not list:
            raise HostControlError("host_mappings_invalid", "Bot accounts must be a list")
        account_by_route = {}
        legacy_group_delivery_shape = "group_delivery_mode" not in section
        raw_group_mode = ("onebot_group" if legacy_group_delivery_shape
                          else _text(section.get("group_delivery_mode"), required=True))
        if raw_group_mode not in ("preview", "onebot_group"):
            raise HostControlError("host_group_delivery_invalid", "group delivery must be preview or onebot_group")
        try:
            for row in raw_accounts or ():
                if type(row) is not dict or set(row) != {"platform_id", "account_id", "bot_id"}:
                    raise ValueError()
                account = HostAccount(_text(row["platform_id"], required=True),
                    route_id(_text(row["account_id"], required=True)),
                    _text(row["bot_id"], required=True))
                route = account.platform_id, account.self_id
                if route in account_by_route:
                    raise ValueError()
                account_by_route[route] = account
            for target in (*targets, *groups):
                account = HostAccount(target.platform_id, route_id(target.account_id), target.bot_id)
                route = account.platform_id, account.self_id
                previous = account_by_route.setdefault(route, account)
                if previous != account:
                    raise ValueError()
        except ValueError:
            raise HostControlError("host_mappings_conflict", "registered Bot accounts must be unique and match legacy windows") from None
        account_bindings = tuple(sorted(account_by_route.values(), key=lambda a: (a.platform_id, a.self_id)))
        if not account_bindings or not personas:
            raise HostControlError("host_mappings_required", "register at least one Bot account and persona")
        accounts = {(item.platform_id, item.self_id): item.bot_id for item in account_bindings}
        routes, ids, roles = set(), set(), {}
        try:
            for target in targets:
                HostAccount(target.platform_id, route_id(target.account_id), target.bot_id)
                if target.participant_id != "*":
                    route_id(target.participant_id)
                if not PROFILE_PATTERN.fullmatch(target.target_id) or target.target_id in ids or target.route_key in routes:
                    raise ValueError()
                ids.add(target.target_id)
                routes.add(target.route_key)
                account = target.platform_id, target.account_id
                if accounts.setdefault(account, target.bot_id) != target.bot_id:
                    raise ValueError()
            for target in groups:
                if target.target_id in ids:
                    raise ValueError()
                ids.add(target.target_id)
                account = target.platform_id, target.account_id
                if accounts.setdefault(account, target.bot_id) != target.bot_id:
                    raise ValueError()
            for persona in personas:
                mapping = persona.bot_id, persona.persona_id
                if mapping in roles or persona.bot_id not in accounts.values():
                    raise ValueError()
                roles[mapping] = persona.role_id
            if not {p.bot_id for p in personas}.issubset(set(accounts.values())):
                raise ValueError()
        except ValueError:
            raise HostControlError("host_mappings_conflict", "accounts, target routes and native persona mappings must be unambiguous") from None
        capabilities = section.get("capabilities", [*sorted(REQUIRED_CAPABILITIES), "memory.read", "memory.propose",
                                                    "memory.reflect", "memory.subscribe"])
        if (type(capabilities) is not list or any(type(item) is not str for item in capabilities)
                or len(set(capabilities)) != len(capabilities)
                or not REQUIRED_CAPABILITIES.issubset(capabilities) or not set(capabilities).issubset(ALLOWED_CAPABILITIES)):
            raise HostControlError("host_capabilities_invalid", "configure registered non-core capabilities including reply prerequisites")
        window_links = _parse_window_links(section.get("window_links"), targets)
        raw_personas = tuple(personas)
        canonical_personas = _shared_persona_roles(raw_personas)
        aliases_by_old = {}
        for raw, canonical in zip(raw_personas, canonical_personas):
            if not raw.role_id or not canonical.role_id:
                continue
            aliases_by_old.setdefault(raw.role_id, set()).add(canonical.role_id)
        role_aliases = tuple(sorted(
            (old_role, next(iter(destinations)))
            for old_role, destinations in aliases_by_old.items()
            if len(destinations) == 1 and old_role not in destinations
        ))
        return cls(enabled, key, tuple(sorted(targets, key=lambda t: t.target_id)),
                   tuple(sorted(canonical_personas, key=lambda p: (p.bot_id, p.persona_id))), account_bindings,
                   tuple(sorted(capabilities)), common, groups, "onebot_group", window_links,
                   _role_aliases=role_aliases,
                   _legacy_group_delivery_shape=legacy_group_delivery_shape or raw_group_mode == "preview")

    def require_ready(self):
        if not self.enabled:
            raise HostControlError("host_disabled", "enable the shared host before initialization or startup")
        if not self.common.provider_id:
            raise HostControlError("host_provider_required", "select the shared model provider")

    def target(self, target_id):
        found = next((item for item in self.all_targets if item.target_id == target_id), None)
        if found is None:
            raise HostControlError("host_target_unavailable", "the conversation target is not configured")
        return found

    def window_link_for_target(self, target_id):
        return next((link for link in self.window_links if target_id in link.alias_target_ids), None)

    def window_links_for_primary(self, target_id):
        return tuple(link for link in self.window_links if link.primary_target_id == target_id)

    def effective_private_target(self, target):
        link = self.window_link_for_target(target.target_id)
        return self.target(link.primary_target_id) if link is not None and link.mode == "primary" else target

    def private_target(self, platform_id, account_id, participant_id):
        exact = next((item for item in self.targets if item.route_key ==
                      (platform_id, account_id, participant_id)), None)
        if exact is not None:
            return exact
        template = next((item for item in self.targets if item.route_key ==
                         (platform_id, account_id, "*")), None)
        if template is None:
            return None
        account = next((item for item in self.accounts if
                        (item.platform_id, item.self_id) == (platform_id, account_id)), None)
        if account is None or account.bot_id != template.bot_id:
            return None
        identifier = "private-" + digest([platform_id, account_id, participant_id])[:24]
        return PrivateTarget(identifier, platform_id, account_id, template.bot_id, participant_id)

    def group_target(self, platform_id, account_id, group_id):
        exact = next((item for item in self.group_targets if item.route_key ==
                      (platform_id, account_id, group_id)), None)
        if exact is not None:
            return exact
        template = next((item for item in self.group_targets if item.route_key ==
                         (platform_id, account_id, "*")), None)
        if template is None:
            return None
        account = next((item for item in self.accounts if
                        (item.platform_id, item.self_id) == (platform_id, account_id)), None)
        if account is None or account.bot_id != template.bot_id:
            return None
        identifier = "group-" + digest([platform_id, account_id, group_id])[:24]
        return GroupTarget(identifier, platform_id, account_id, template.bot_id, group_id)

    def template_for(self, target):
        rows = self.group_targets if isinstance(target, GroupTarget) else self.targets
        peer = target.group_id if isinstance(target, GroupTarget) else target.participant_id
        field = "group_id" if isinstance(target, GroupTarget) else "participant_id"
        account_rows = (item for item in rows if item.platform_id == target.platform_id
                        and item.account_id == target.account_id and item.bot_id == target.bot_id)
        exact = next((item for item in account_rows if getattr(item, field) == peer), None)
        if exact is not None:
            return exact
        return next((item for item in rows if getattr(item, field) == "*"
                     and item.platform_id == target.platform_id and item.account_id == target.account_id
                     and item.bot_id == target.bot_id), None)
