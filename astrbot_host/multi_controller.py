"""Shared-installation lifecycle and explicit native multi-target admission."""

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import traceback
from uuid import uuid4

from companion_v2.adapters.astrbot.native import (
    HostPersona, NativePrivateBridge, bounded_native_lookup, event_account_id, event_peer_id,
    native_client_connected, persona_instruction, supported_platform_name,
)
from companion_v2.adapters.astrbot.group import group_event_route
from companion_v2.adapters.astrbot.provider import provider_fingerprint
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers
from companion_v2.execution.models import TaskContext, TaskError, digest, require
from companion_v2.conversation.pokes import PokePolicy
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, SubjectKind, SubjectRef
from .integration_registry import IntegrationRegistry
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.proactive.search_settings import SearchSettingsStore
from .multi_authority import HOST_ID, MANIFEST, MultiAuthority
from .multi_session import MultiHostSession, persona_conversation
from .multi_settings import MultiHostSettings, find_persona_mapping
from .bootstrap_provider import resolve_bootstrap_provider
from .group_settings import GroupTarget
from .native import installed_native_types, native_text_provider
from .backup_api import guard_restore_startup
from .storage_location import resolve_data_root, selected_backup_directory
from .native_extensions import NativeExtensionRegistry, together_execution_binding
from .native_extensions.builtins import MANAGED_EXTENSIONS, create_bundled_extensions
from .settings import DELIVERY_AUTHORIZATION_TTL, INSTALLATION_PREFIX, PROFILE_PATTERN, HostControlError
from .binding import describe_pin, event_identity, is_private_message, message_text, parse_bind_command
from .calendar_command import receive_calendar_command
from .binding_store import PendingBinding, binding_path, clear_binding, read_binding, write_binding
from .news import configured_news_port
from .service_balance import configured_balance_port
from .weather_settings import configured_weather_ports
from .files.parsing import parser_availability
from .qzone_transport import build_qzone_transport
from .solar_terms import configured_solar_term_port
from .session_topology import SessionTopology
from .shared_host_migration import recover_repeated_single_target


logger = logging.getLogger(__name__)


class AstrBotMultiController:
    def __init__(self, context, config, data_root, *, native_types=None, now=None):
        self.lifecycle_token = uuid4().hex
        self.config = config
        # Hosts may hand us the plugin code tree or a V1 data root when
        # get_data_dir() is incomplete. Normalize again here so every caller
        # (not only main.py) lands in an isolated V2 data root.
        self.context, self.data_root = context, resolve_data_root(Path(data_root).resolve())
        self.integration = IntegrationRegistry(context)
        self.native_types = native_types or installed_native_types()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.runtime = self.authority = self.session = self.last_session = None
        # The dashboard can report whether a staged restore was applied at open.
        self.last_restore = None
        # Diagnostics for the startup gate: set before opening, kept on failure so
        # the operator can see which journal/pending request blocked the store.
        self.restore_state = None
        self.restore_blocked = False
        self.native_extensions = None
        self._accepting = self._terminated = False
        # Lifecycle work may wait on AstrBot; keep that wait out of the lock
        # shared by Dashboard data APIs.
        self._control_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self.session_topology = SessionTopology(self)
        self._storage_profile_root = None
        self._adopting_legacy_profile = False
        self.last_failure = None
        self._termination_task = None
        self.poke_policy = PokePolicy()
        try:
            self.settings, self._storage_profile_root, self._adopting_legacy_profile = self._resolve_settings(config)
            self.poke_policy = PokePolicy.parse(config.get("poke"))
            self.settings_error = None
        except (HostControlError, ValueError) as error:
            self.settings = None
            self.settings_error = (error if isinstance(error, HostControlError) else
                                   HostControlError("host_poke_settings_invalid", str(error)))
        from .multi_api import MultiHostWebAPI
        self.web_api = MultiHostWebAPI(self)

    def refresh_settings(self):
        try:
            self.settings, self._storage_profile_root, self._adopting_legacy_profile = self._resolve_settings(self.config)
            self.poke_policy = PokePolicy.parse(self.config.get("poke"))
            self.settings_error = None
            if self.authority is not None:
                self.authority.settings = self.settings
        except (HostControlError, ValueError) as error:
            self.settings = None
            self._storage_profile_root = None
            self._adopting_legacy_profile = False
            self.settings_error = (error if isinstance(error, HostControlError) else
                                   HostControlError("host_poke_settings_invalid", str(error)))

    def _resolve_settings(self, config):
        settings = MultiHostSettings.parse(config)
        # AstrBot already has a deterministic current/default text provider.
        # Use it as the shared host's read-only bootstrap default so account
        # mapping works without a redundant save on a second configuration
        # screen. An explicitly saved provider still wins.
        bootstrap = resolve_bootstrap_provider(
            self.context, getattr(self.native_types, "is_text_provider", None), settings.common.provider_id)
        if (not settings.common.provider_id and bootstrap.source == "auto"
                and bootstrap.provider_id):
            settings = replace(settings, common=replace(
                settings.common, provider_id=bootstrap.provider_id))
        shared_root = self.data_root / "installations" / settings.installation_key
        legacy = config.get("staging", {}) if isinstance(config, dict) else {}
        legacy_id = legacy.get("profile_id") if isinstance(legacy, dict) else None
        if type(legacy_id) is str and PROFILE_PATTERN.fullmatch(legacy_id):
            legacy_root = self.data_root / "profiles" / legacy_id
            legacy_database = legacy_root / "runtime" / StorageRuntime.DATABASE_FILENAME
            shared_database = shared_root / "runtime" / StorageRuntime.DATABASE_FILENAME
            if legacy_database.is_file() and not shared_database.is_file():
                return (replace(settings, _storage_installation_id=INSTALLATION_PREFIX + legacy_id),
                        legacy_root, True)
        return settings, shared_root, False

    def role_source_target_id(self, role_id, fallback_target_id):
        """Resolve one stable private window as the role-level data source.

        State, life, model and the other role APIs are owned by the role, not by
        whichever window happens to be in the URL.  A wildcard template is only
        a route factory and must never become the source of role-level data.
        """
        settings = self.settings
        if settings is None:
            return fallback_target_id
        fallback = settings.target(fallback_target_id)
        if getattr(fallback, "participant_id", None) != "*":
            return fallback_target_id
        candidates = [target for target in settings.targets if target.participant_id != "*"
                      and any(mapping.bot_id == target.bot_id and mapping.role_id == role_id
                              for mapping in settings.personas)]
        if not candidates:
            return fallback_target_id
        return sorted(candidates, key=lambda target: target.target_id)[0].target_id

    def _settings(self):
        if self._terminated:
            raise TaskError("host_terminated", "the shared host is stopping or terminated")
        if self.settings_error is not None:
            raise self.settings_error
        if self.settings is not None and not self.settings.common.provider_id:
            # Provider loading can finish after plugin construction. Resolve
            # again at first use so a blank reviewed setting follows AstrBot's
            # current model instead of failing the mapping-to-start flow.
            bootstrap = resolve_bootstrap_provider(
                self.context, getattr(self.native_types, "is_text_provider", None), "")
            if bootstrap.source == "auto" and bootstrap.provider_id:
                self.settings = replace(self.settings, common=replace(
                    self.settings.common, provider_id=bootstrap.provider_id))
                if self.authority is not None:
                    self.authority.settings = self.settings
        self.settings.require_ready()
        return self.settings

    def startup_readiness(self):
        """Return a retryable readiness code until every configured Bot is online."""
        for account in self._settings().accounts:
            platform = self.context.get_platform_inst(account.platform_id)
            if platform is None:
                return "host_platform_unavailable"
            meta = platform.meta()
            if not supported_platform_name(meta.name) or meta.id != account.platform_id:
                return "host_platform_changed"
            if not native_client_connected(platform, account.self_id):
                return "host_account_unavailable"
        return None

    @property
    def profile_root(self):
        return self._storage_profile_root if self.settings else None

    @property
    def _multi_host(self):
        """This controller owns a shared installation with one core admin window."""
        return True

    @property
    def binding_root(self):
        return self.data_root / "setup"

    def _parked_binding(self):
        return read_binding(self.binding_root)

    def binding_state(self):
        parked = self._parked_binding()
        if parked is None:
            if self.runtime is not None and self.authority is not None:
                try:
                    target = self._resolve_proactive_diagnostic_target()
                except (TaskError, HostControlError) as error:
                    return {"bound": False, "pinned": False, "pending": False,
                            "confirmed": False, "error": getattr(error, "code", "host_binding_unavailable")}
                settings = self.settings
                personas = [item for item in settings.personas if item.bot_id == target.bot_id]
                role_ids = {item.role_id for item in personas}
                persona_ids = {item.persona_id for item in personas}
                return {"bound": True, "pinned": False, "pending": False, "confirmed": True,
                        "recovered": True, "verification": "persistent_core_identity",
                        "configured_target": True, "platform_id": target.platform_id,
                        "account_id": target.account_id, "participant_id": target.participant_id,
                        "bot_id": target.bot_id, "role_id": next(iter(role_ids)) if len(role_ids) == 1 else "",
                        "persona_id": next(iter(persona_ids)) if len(persona_ids) == 1 else ""}
            return {"bound": False, "pinned": False, "pending": False, "confirmed": False}
        try:
            identity = parked.identity()
        except HostControlError as error:
            return {"bound": False, "pinned": False, "pending": False, "error": error.code}
        configured_target = self._core_target(parked) is not None
        return {"bound": parked.confirmed, "pinned": False, "pending": not parked.confirmed,
                "confirmed": parked.confirmed, "configured_target": configured_target,
                **self._reviewed_identity(identity)}

    def _reviewed_identity(self, identity):
        """The role a confirmed window actually routes to after the last review.

        A persona that was renamed or regrouped into another role keeps the parked
        file's old key.  Comparing that stale key against the live role made
        开始使用 report host_persona_binding_changed forever, even though the window
        itself was still the confirmed one, so resolve it through the reviewed map.
        """
        current = dict(identity)
        settings = self.settings
        if settings is None:
            return current
        from .identity_derivation import normalize_persona_id
        wanted = normalize_persona_id(current.get("persona_id"))
        mapping = next((item for item in settings.personas
                        if item.bot_id == current["bot_id"]
                        and normalize_persona_id(item.persona_id) == wanted), None)
        current["role_id"] = (mapping.role_id if mapping is not None
                              else settings.canonical_role_id(current["role_id"]))
        return current

    def _core_target(self, binding):
        settings = self.settings
        if settings is None:
            return None
        match = next((target for target in settings.targets
                      if (target.platform_id, target.account_id, target.participant_id)
                      == (binding.platform_id, binding.account_id, binding.participant_id)), None)
        if match is None:
            return None
        if binding.persona_id:
            from .identity_derivation import normalize_persona_id
            wanted = normalize_persona_id(binding.persona_id)
            if not any(persona.bot_id == match.bot_id
                       and normalize_persona_id(persona.persona_id) == wanted
                       for persona in settings.personas):
                return None
        return match

    async def proactive_diagnostic_target(self):
        """Resolve a unique configured private route owned by a current core user.

        The setup binding file can be lost independently of the shared identity
        ledger. A diagnostic may recover its recipient from that ledger, but only
        when exactly one explicit private target still resolves to a core identity.
        """
        await self._open_async()
        return await asyncio.to_thread(self._resolve_proactive_diagnostic_target)

    def _resolve_proactive_diagnostic_target(self):
        settings = self._settings()
        if self.runtime is None or self.authority is None:
            raise TaskError("setup_binding_confirmation_required",
                            "the shared identity store is not available to verify a core administrator")

        from companion_v2.identity_policy.models import AuthorizationTier

        core_subjects = {row["subject_id"] for row in self.runtime.read_all(
            "SELECT subject_id FROM subject_access_tiers WHERE subject_kind = 'human' AND tier = ?",
            (AuthorizationTier.CORE.value,),
        )}
        candidates = []
        for target in settings.primary_targets:
            if target.participant_id == "*":
                continue
            subject = self.authority.subject(target)
            if subject.subject_id not in core_subjects:
                continue
            try:
                actor = self.authority.actor(target)
            except TaskError:
                continue
            if actor.subject == subject:
                candidates.append(target)

        if len(candidates) != 1:
            code = "setup_core_window_ambiguous" if candidates else "setup_binding_confirmation_required"
            reason = ("more than one configured private target belongs to a core administrator"
                      if candidates else
                      "confirm the private window or configure one exact target for a core administrator")
            raise TaskError(code, reason)
        return candidates[0]

    def _register_core_binding(self, binding):
        """Seed the first shared target from the Dashboard-confirmed private window."""
        from copy import deepcopy
        from .identity_derivation import derive_core_target_id, derive_role_id

        if binding.persona_id:
            native = self.context.persona_manager.get_persona_v3_by_id(binding.persona_id)
            if native is None or native.get("name") != binding.persona_id:
                raise TaskError("host_persona_unavailable", "choose a persona that is currently loaded in AstrBot")
        if self._core_target(binding):
            return
        save = getattr(self, "save_plugin_config", None)
        if not callable(save):
            raise TaskError("setup_config_save_unavailable", "restart the plugin with Dashboard configuration support")
        if not isinstance(self.config, dict):
            raise TaskError("setup_config_unavailable", "the plugin configuration cannot be edited here")

        candidate = deepcopy(dict(self.config))
        section = candidate.setdefault("multi_host", {})
        if type(section) is not dict:
            raise TaskError("setup_config_invalid", "the shared host settings have an invalid shape")
        section["enabled"] = True
        targets = section.setdefault("targets", [])
        personas = section.setdefault("personas", [])
        group_targets = section.setdefault("group_targets", [])
        if any(type(items) is not list for items in (targets, personas, group_targets)):
            raise TaskError("setup_config_invalid", "the account and persona mappings have an invalid shape")

        account_rows = [item for item in [*targets, *group_targets]
                        if type(item) is dict
                        and item.get("platform_id") == binding.platform_id
                        and item.get("account_id") == binding.account_id]
        bot_ids = {item.get("bot_id") for item in account_rows if type(item.get("bot_id")) is str}
        if len(bot_ids) > 1:
            raise TaskError("host_mappings_conflict", "this Bot account is already assigned to conflicting V2 identities")
        identity = binding.identity()
        bot_id = next(iter(bot_ids), identity["bot_id"])
        target = next((item for item in targets if type(item) is dict
                       and (item.get("platform_id"), item.get("account_id"), item.get("participant_id"))
                       == (binding.platform_id, binding.account_id, binding.participant_id)), None)
        if target is None:
            used_ids = {item.get("target_id") for item in targets if type(item) is dict}
            base_id = derive_core_target_id(binding.platform_id, binding.account_id, binding.participant_id)
            target_id = base_id
            suffix = 2
            while target_id in used_ids:
                target_id = f"{base_id}-{suffix}"
                suffix += 1
            targets.append({"target_id": target_id, "platform_id": binding.platform_id,
                            "account_id": binding.account_id, "bot_id": bot_id,
                            "participant_id": binding.participant_id})
        elif target.get("bot_id") != bot_id:
            raise TaskError("host_mappings_conflict", "the core administrator window has a conflicting Bot identity")

        persona_id = binding.persona_id or "[%None]"
        persona = next((item for item in personas if type(item) is dict
                        and (item.get("bot_id"), item.get("persona_id")) == (bot_id, persona_id)), None)
        if persona is None:
            personas.append({"bot_id": bot_id, "persona_id": persona_id,
                             "role_id": derive_role_id(binding.account_id, binding.persona_id)})

        from .multi_settings import MultiHostSettings
        MultiHostSettings.parse(candidate)
        save(candidate)
        self.refresh_settings()
        if self.settings_error is not None:
            raise TaskError(self.settings_error.code, str(self.settings_error))

    def handles_binding(self, event):
        from .recalls import is_notice_event
        return (not is_notice_event(event) and is_private_message(event)
                and parse_bind_command(message_text(event)).kind != "ignore")

    async def receive_binding(self, event):
        request = parse_bind_command(message_text(event))
        if request.kind == "ignore":
            return {"handled": False}
        identity = event_identity(event)
        if identity is None:
            return {"handled": True, "ok": False, "reason": "host_identity_unreadable",
                    "reply": "这条消息读不到平台、Bot 账号或发送者，无法作为绑定。"}
        existing = self._parked_binding()
        if request.kind == "unbind":
            if existing is not None and existing.confirmed:
                return {"handled": True, "ok": False, "reason": "host_binding_exists",
                        "reply": "核心管理员窗口已确认；请先在「开始使用」重置首次设置，再更换窗口。"}
            removed = clear_binding(self.binding_root)
            return {"handled": True, "ok": True, "removed": removed,
                    "reply": "已清除待绑定窗口。" if removed else "当前没有待绑定窗口。",
                    "binding": self.binding_state()}
        if request.reason == "host_identity_invalid":
            return {"handled": True, "ok": False, "reason": request.reason,
                    "reply": "人格名不能包含换行或过长，请用一行短名字重发。"}
        persona_id = request.persona_id
        if request.kind == "bind" and not persona_id:
            # 不带人格名时固定这个窗口此刻真实使用的人格。空字符串在运行时只会
            # 变成 "[%None]"，窗口一旦解析出真实人格就会因未映射而无法回复。
            persona_id = await self._window_persona(identity)
        candidate = PendingBinding(identity["platform_id"], identity["account_id"],
                                   identity["participant_id"], persona_id)
        if request.kind == "confirm":
            if existing is None:
                return {"handled": True, "ok": False, "reason": "host_binding_missing",
                        "reply": "还没有待确认的核心管理员窗口，先发送「我会永远陪着你」。"}
            expected = (existing.platform_id, existing.account_id, existing.participant_id)
            actual = (identity["platform_id"], identity["account_id"], identity["participant_id"])
            return {"handled": True, "ok": expected == actual,
                    "reason": None if expected == actual else "host_binding_confirmation_mismatch",
                    "reply": "已核对这条私聊的来源。请回到「开始使用」，由 Dashboard 管理员完成确认。"
                    if expected == actual else "确认消息与待绑定的平台、Bot 账号或私聊对象不一致。"}
        if existing is not None:
            same = (existing.platform_id, existing.account_id, existing.participant_id,
                    existing.persona_id) == (candidate.platform_id, candidate.account_id,
                                               candidate.participant_id, candidate.persona_id)
            if same:
                return {"handled": True, "ok": True, "confirmed": existing.confirmed,
                        "reply": "这条核心管理员窗口已经待确认。请回到「开始使用」查看并确认。",
                        "binding": self.binding_state()}
            return {"handled": True, "ok": False, "reason": "host_binding_candidate_exists",
                    "reply": "已有待确认的核心管理员窗口。请先发送 companion_unbind，再重新绑定。"}
        try:
            write_binding(self.binding_root, candidate)
            identity = candidate.identity()
        except (HostControlError, OSError, ValueError) as error:
            code = getattr(error, "code", "host_binding_write_failed")
            return {"handled": True, "ok": False, "reason": code,
                    "reply": "这条绑定无法成立，请回到「开始使用」查看原因。"}
        return {"handled": True, "ok": True, "confirmed": False,
                "reply": describe_pin(identity), "binding": self.binding_state()}

    async def _window_persona(self, identity):
        """The persona this private window actually speaks in, when unnamed."""
        resolver = getattr(self.session_topology, "window_persona", None)
        if not callable(resolver):
            return ""
        try:
            return await resolver(identity["platform_id"], identity["participant_id"])
        except Exception as error:
            logger.debug("[CompanionV2] 绑定时读取当前人格失败 error_type=%s", type(error).__name__)
            return ""

    def confirm_binding(self):
        parked = self._parked_binding()
        if parked is None:
            binding = self.binding_state()
            if binding.get("recovered"):
                return {"confirmed": True, "binding": binding}
            return {"confirmed": False, "reason": "setup_binding_missing",
                    "detail": "send 我会永远陪着你 from the core administrator private window first"}
        try:
            self._register_core_binding(parked)
        except (TaskError, HostControlError, ValueError, TypeError) as error:
            return {"confirmed": False, "reason": getattr(error, "code", "setup_core_window_not_configured"),
                    "detail": str(error)}
        if not self._core_target(parked):
            return {"confirmed": False, "reason": "setup_core_window_not_configured",
                    "detail": "the confirmed private window could not be added to the shared host"}
        if not parked.confirmed:
            write_binding(self.binding_root, PendingBinding(parked.platform_id, parked.account_id,
                parked.participant_id, parked.persona_id, confirmed=True))
        return {"confirmed": True, "binding": self.binding_state()}

    def _failure(self, error, *, operation=""):
        code = (error.code if isinstance(error, (TaskError, HostControlError)) else
                "host_module_unavailable" if isinstance(error, ImportError) else "host_control_failed")
        if code in ("host_control_failed", "host_module_unavailable"):
            # Keep the failing source locations, without logging exception values
            # or locals that may contain native credentials or conversation text.
            logger.error("[CompanionV2] 宿主操作异常 operation=%s code=%s error_type=%s\n%s",
                         operation or "control", code, type(error).__name__,
                         "".join(traceback.format_tb(error.__traceback__)))
        reason = ("插件模块未能导入，请重新加载 V2 插件；若仍失败，请检查安装包是否完整并查看 AstrBot 日志。"
                  if code == "host_module_unavailable" else
                  str(error) if code.split("__", 1)[0] == "host_native_lookup_timeout"
                  or code in ("host_binding_changed", "account_config_save_failed", "account_config_persistence_unavailable",
                              "backup_restore_rollback_incomplete", "backup_restore_journal_unreadable",
                              "backup_restore_startup_blocked") else
                  "shared host configuration, native route or authority could not be verified")
        self.last_failure = {"code": code, "reason": reason}
        if operation:
            self.last_failure["operation"] = operation
        return Outcome.failed(code, reason)

    async def _probe(self, settings=None, *, include_conversations=True, include_window_personas=True,
                     allow_unavailable_model=False):
        settings = settings or self._settings()
        personas = []
        for mapping in settings.personas:
            native = (None if mapping.persona_id == "[%None]" else
                      self.context.persona_manager.get_persona_v3_by_id(mapping.persona_id))
            if mapping.persona_id != "[%None]" and (native is None or native.get("name") != mapping.persona_id):
                raise TaskError("host_persona_unavailable", "a configured native persona could not be resolved")
            personas.append(HostPersona(mapping.bot_id, mapping.persona_id,
                SubjectRef(settings.installation_id, SubjectKind.BOT, mapping.role_id), digest(persona_instruction(native))))
        bridge = NativePrivateBridge(self.context, version=self.native_types.version, event_type=self.native_types.event,
            installation_id=settings.installation_id, host_id=HOST_ID, identities=None,
            accounts=settings.accounts, personas=tuple(personas), now=self.now,
            session_persona_override_lookup=self.native_types.session_persona_override_lookup,
            persona_snapshot_lookup=self.native_types.persona_snapshot_lookup,
            persona_selection_lookup=self.native_types.persona_selection_lookup)
        conversations = {}
        for account in settings.accounts:
            platform = self.context.get_platform_inst(account.platform_id)
            client = bridge.platforms[account.platform_id][1]
            if (platform is None or platform.get_client() is not client
                    or not bridge.account_connected(account, client)):
                raise TaskError("host_account_unavailable", "a configured account is not connected")
        for target in (settings.all_targets if include_conversations else ()):
            account = bridge.accounts[(target.platform_id, target.account_id)]
            peer = target.participant_id if hasattr(target, "participant_id") else target.group_id
            if peer == "*":
                continue
            if target in settings.targets:
                umo = bridge._umo(target.platform_id, target.participant_id)
                # Global profile preparation provisions every configured role; the window
                # persona is resolved later for live input admission.
                conversation_manager = self.context.conversation_manager
                conversation_id = await bounded_native_lookup(
                    conversation_manager.get_curr_conversation_id(umo), operation="conversation_id")
                verified_conversation_id = await bounded_native_lookup(
                    conversation_manager.get_curr_conversation_id(umo), operation="conversation_verify")
                if verified_conversation_id != conversation_id:
                    raise TaskError("host_conversation_changed", "host conversation changed during startup probe")
                conversations[target.target_id] = {
                    "target": target, "conversation_id": bridge._conversation_key(umo, conversation_id),
                    "role_ids": tuple(sorted({item.role_id for item in settings.personas if item.bot_id == target.bot_id})),
                }
            # Groups resolve the actual event's UMO, including member sessions,
            # at admission. No human speaker is fabricated during startup.
        persona_inventory = []
        if include_conversations and include_window_personas:
            try:
                persona_inventory = (await self.session_topology.inventory_report(settings=settings))["targets"]
            except Exception as error:
                logger.warning("[CompanionV2] 启动时人格快照未完整刷新 error_type=%s", type(error).__name__)
        if (include_conversations and include_window_personas
                and any(target.participant_id == "*" for target in settings.targets)):
            inventory = [row for row in persona_inventory if row.get("scope_kind") == "private"]
            for window in inventory:
                if window.get("native") is not True or window.get("scope_kind") != "private":
                    continue
                target = settings.private_target(window.get("platform_id"), window.get("account_id"),
                                                 window.get("participant_id"))
                if target is None or target.participant_id == "*" or window.get("bot_id") != target.bot_id:
                    continue
                mapping = find_persona_mapping(settings.personas, target.bot_id, window.get("persona_id"))
                if mapping is None:
                    continue
                conversations[target.target_id] = {
                    "target": target,
                    "conversation_id": bridge._conversation_key(window["unified_msg_origin"], window.get("conversation_id")),
                    "role_ids": (mapping.role_id,),
                }
        provider_id = settings.common.provider_id
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        model_issue = None
        if not native_text_provider(self.native_types, provider):
            model_issue = "host_provider_unavailable" if provider_id else "host_provider_required"
            if not allow_unavailable_model:
                reason = ("the configured model has no verified native adapter" if provider_id
                          else "select a loaded AstrBot text provider")
                raise TaskError(model_issue, reason)
        else:
            try:
                assemble_providers(self.context, bridge=None, native_types=self.native_types,
                                   primary_id=provider_id, additional=settings.common.additional_providers,
                                   defaults=TextProviderSettings(provider_id, settings.common.tariff,
                                       settings.common.max_output_tokens, settings.common.maximum_input_estimate,
                                       settings.common.output_parameter, settings.common.streaming))
            except (TaskError, TypeError, ValueError, AttributeError, KeyError) as error:
                if not allow_unavailable_model:
                    raise
                model_issue = getattr(error, "code", "host_provider_unavailable")
        manifest = {"binding_contract": settings.binding_contract,
                    "settings_fingerprint": settings.fingerprint,
                    "provider_fingerprint": provider_fingerprint(provider) if model_issue != "host_provider_required"
                        and provider is not None and native_text_provider(self.native_types, provider) else "",
                    "personas": [asdict(persona) for persona in personas]}
        manifest["fingerprint"] = digest(manifest)
        # Account/persona mappings have their own validity. Keep a pending model
        # issue out of the signed manifest so a later model selection can start
        # the already-saved mapping without changing its identity contract.
        if model_issue is not None:
            manifest["_model_issue"] = model_issue
        # Native reads may outlive termination. A reviewed candidate can also be
        # the first valid configuration, so rechecking the old settings here
        # would reject the very mapping that apply_bindings is preparing.
        if self._terminated:
            raise TaskError("host_terminated", "the shared host is stopping or terminated")
        return manifest, conversations

    def _open(self):
        if self.runtime is None:
            self.runtime, self.authority, self.last_restore = self._create_storage()

    def _create_storage(self):
        settings = self._settings()
        root = self.profile_root
        if root is None:
            raise HostControlError("staging_settings_invalid", "a profile directory could not be resolved")
        # Apply restores before opening SQLite, while replacing the database is safe.
        storage = self.config.get("storage", {}) if isinstance(self.config, dict) else {}
        backup_directory = selected_backup_directory(
            root / "runtime", storage.get("backup_path", "") if isinstance(storage, dict) else "",
            settings.installation_id, plugin_root=self.data_root)
        last_restore, restore_state = guard_restore_startup(
            root / "runtime", installation_id=settings.installation_id,
            backup_directory=backup_directory)
        self.last_restore, self.restore_state = last_restore, restore_state
        if restore_state.get("blocking"):
            # A restore whose rollback did not finish is not "no restore": the
            # journal holds the previous installation. StorageRuntime.open would
            # create a new empty database for any missing file and fork the data.
            # Keep the staged request, the journal and every byte, report a
            # readable code, and let the next start retry the same rollback.
            self.restore_blocked = True
            logger.error(
                "[CompanionV2] 拒绝在还原回滚未完成时打开数据库 code=%s journals=%s pending=%s root=%s",
                restore_state.get("code"),
                ",".join(item.get("name", "?") for item in restore_state.get("journals") or ()),
                bool(restore_state.get("pending")), root / "runtime")
            raise HostControlError(restore_state["code"], restore_state["detail"])
        self.restore_blocked = False
        runtime = StorageRuntime.open(data_root=root / "runtime", installation_id=settings.installation_id)
        runtime.backup_directory = backup_directory
        try:
            from companion_v2.expression.configuration import migrate_legacy_policy_values
            from companion_v2.expression.reaction_configuration import migrate_window_values
            migrate_legacy_policy_values(runtime)
            migrate_window_values(runtime)
        except BaseException:
            runtime.close()
            raise
        return runtime, MultiAuthority(runtime, settings, now=self.now,
            platform_name=self._configured_platform_name), last_restore

    def _configured_platform_name(self, platform_id):
        platform = self.context.get_platform_inst(platform_id)
        return str(platform.meta().name) if platform is not None else ""

    async def _open_async(self):
        if self.runtime is None:
            opened = await asyncio.to_thread(self._create_storage)
            if self.runtime is None:
                self.runtime, self.authority, self.last_restore = opened
            else:
                opened[0].close()

    async def _assemble(self, manifest):
        personas = tuple(HostPersona(item["bot_id"], item["persona_id"],
            SubjectRef(item["role"]["installation_id"], SubjectKind.BOT, item["role"]["subject_id"]), item["instruction_hash"])
            for item in manifest["personas"])
        if self.native_extensions is not None:
            await self.native_extensions.aclose()
        bundled_extensions, bundled_issues = create_bundled_extensions(self.runtime.data_root, now=self.now, context=self.context, controller=self)
        self.native_extensions = NativeExtensionRegistry(self.context, now=self.now,
            together_execution=together_execution_binding(self.settings.common),
            image_settings_path=self.data_root / "model_image_routes.json",
            bundled_extensions=bundled_extensions, bundled_issues=bundled_issues,
            managed_extensions=MANAGED_EXTENSIONS)
        search_provider_resolver = SearchSettingsStore(self.data_root).selected_provider_id
        self.session = MultiHostSession(self.runtime, self.context, settings=self.settings, personas=personas,
            authority=self.authority, native_types=self.native_types, provider_fingerprint=manifest["provider_fingerprint"],
            runtime_instance_id="astrbot-multi-" + uuid4().hex, native_tool_port=self.native_extensions, now=self.now,
            persona_window_snapshot_lookup=self.session_topology.persona_selection_snapshot,
            persona_window_snapshot_update=self.session_topology.update_persona_selection_snapshot,
            news_port=configured_news_port(self.config, context=self.context,
                deepseek_provider_resolver=search_provider_resolver),
            balance_port=configured_balance_port(self.config),
            weather_ports=configured_weather_ports(self.config),
            qzone_transport=build_qzone_transport(self.context, data_root=self.data_root),
            solar_term_port=configured_solar_term_port(
                self.config.get("solar_terms") if isinstance(self.config, dict) else None))
        from .worker_lifecycle import AstrBotWorkerLifecycle
        self.session.application.tasks.bind_worker_lifecycle(AstrBotWorkerLifecycle())
        # 关键词材料：服务余额、当前模型、链接正文。各自判断可用性，
        # 所以这里无条件登记，不额外做配置判断。
        try:
            from .service_balance import balance_keyword_material
            from .model_route_material import model_route_keyword_material
            from .web_read_material import web_read_keyword_material
            from companion_v2.conversation.keyword_materials import KeywordMaterials
            self.session.application.keyword_materials = KeywordMaterials((
                balance_keyword_material(self.session.application),
                model_route_keyword_material(self.session.application, self.context,
                                             native_tool_port=self.native_extensions),
                web_read_keyword_material(self.session.application),
            ))
        except Exception as error:
            logger.warning("[CompanionV2] 关键词材料登记失败 error_type=%s", type(error).__name__)
        try:
            from .files.parsing import report_parser_availability
            report_parser_availability(logger)
        except Exception as error:
            logger.warning("[CompanionV2] 文档解析组件自检失败 error_type=%s", type(error).__name__)
        from .knowledge_sources import attach_official_knowledge
        attach_official_knowledge(self.session.application, self.context)
        from .semantic_models import NativeSemanticModels, configured_slang_search_port
        self.session.application.attach_semantics(NativeSemanticModels(self.context, self.settings.common.provider_id,
            configured_slang_search_port(self.context, deepseek_provider_resolver=search_provider_resolver)),
            reaction_role_count=len({(item.bot_id, item.role_id) for item in self.settings.personas}))
        from companion_v2.decision import LayaDecisionService, LayaSettingsStore
        from .laya_client import LayaDecisionClient

        def laya_for_context(context):
            return LayaDecisionService(LayaSettingsStore(self.data_root),
                runtime=self.session.application.runtime, origin_key=context.key, client_factory=LayaDecisionClient)

        self.session.application.attach_laya_decision(laya_for_context)

    def _idle(self):
        if self.session is not None and self.session.application.state not in ("new", "stopped"):
            raise TaskError("host_must_stop", "stop the shared host and its outstanding work before changing configuration")

    async def probe(self):
        async with self._lifecycle_lock:
            try:
                self.refresh_settings()
                manifest, _ = await self._probe(include_conversations=True, include_window_personas=False)
                return Outcome.success({"installation_id": self.settings.installation_id,
                    "configuration_fingerprint": manifest["fingerprint"], "targets": len(self.settings.all_targets),
                    "group_targets": len(self.settings.group_targets),
                    "personas": len(self.settings.personas), "model_request_sent": False, "platform_message_sent": False})
            except Exception as error:
                return self._failure(error, operation="probe")

    async def initialize_profile(self):
        async with self._lifecycle_lock:
            try:
                self.refresh_settings()
                manifest, conversations = await self._probe()
                previous_session = self.session
                restore_running = bool(previous_session is not None
                    and previous_session.application.state == "running")
                if previous_session is not None and previous_session.application.state not in ("new", "stopped"):
                    self._accepting = False
                    report = await previous_session.close(grace_seconds=0.5)
                    if getattr(report, "state", None) != "stopped":
                        raise TaskError("host_must_stop", "the shared host did not finish stopping before initialization")
                    async with self._control_lock:
                        if self.session is previous_session:
                            self.last_session, self.session = previous_session, None
                self._idle()
                async with self._control_lock:
                    if self._terminated:
                        raise TaskError("host_terminated", "the shared host is stopping or terminated")
                    self._idle()
                    receipt = await self._initialize_manifest_locked(manifest, conversations)
                    session = self.session
                if restore_running:
                    await self._start_initialized_session(session)
                self.last_failure = None
                return Outcome.success(receipt)
            except Exception as error:
                return self._failure(error, operation="initialize_profile")

    async def _initialize_manifest_locked(self, manifest, conversations):
        await self._open_async()
        receipt = await asyncio.to_thread(self._initialize_manifest_storage, manifest, conversations)
        await self._assemble(manifest)
        return receipt

    def _initialize_manifest_storage(self, manifest, conversations):
        with self.runtime.transaction():
            receipt = self.authority.initialize(manifest, allow_legacy_staging=self._adopting_legacy_profile)
            for conversation in conversations.values():
                target = conversation["target"]
                for role_id in conversation["role_ids"]:
                    role = SubjectRef(self.settings.installation_id, SubjectKind.BOT, role_id)
                    from companion_v2.foundation.refs import ScopeKind, ScopeRef
                    scope = ScopeRef(self.settings.installation_id, target.platform_id, ScopeKind.PRIVATE,
                        target.participant_id, target.bot_id, target.account_id,
                        persona_conversation(conversation["conversation_id"], role), target.participant_id)
                    self.authority.provision_scope(target, scope, role)
        return receipt

    async def apply_bindings(self, candidate, *, expected_config=None):
        """Apply Dashboard-reviewed routes while retaining existing native owners."""
        was_accepting = self._accepting
        try:
            return await self._apply_reviewed_bindings(candidate, expected_config=expected_config)
        except TaskError as error:
            # A concurrent configuration edit can arrive while close() drains
            # workers. Reject the stale candidate, then reopen the saved routes
            # after releasing the lifecycle lock instead of leaving them stopped.
            if (error.code == "legacy_plan_stale" and was_accepting and not self._terminated
                    and self.session is not None and self.session.application.state == "stopped"):
                self.refresh_settings()
                recovered = await self.start()
                if recovered.failure is not None:
                    logger.warning("[CompanionV2] 映射保存冲突后恢复运行失败 code=%s", recovered.failure.code)
            raise

    async def _apply_reviewed_bindings(self, candidate, *, expected_config=None):
        result = None
        settings = None
        async with self._lifecycle_lock:
            # A reviewed mapping change is itself a lifecycle operation.  Stop
            # the current runtime inside this operation and bring the new
            # mapping back up before returning to the page, so a save never
            # leaves the Dashboard showing a stale or half-loaded host.
            async with self._control_lock:
                if self._terminated:
                    raise TaskError("host_terminated", "the shared host is stopping or terminated")
                if expected_config is not None and self.config != expected_config:
                    raise TaskError("legacy_plan_stale", "插件配置在等待保存期间已变化，请重新试算。")
                settings, profile_root, adopting_legacy_profile = self._resolve_settings(candidate)
                save = getattr(self, "save_plugin_config", None)
                if not callable(save):
                    raise TaskError("account_config_persistence_unavailable", "the host has no plugin configuration writer")
            manifest, _ = await self._probe(settings, include_conversations=False,
                                            allow_unavailable_model=True)
            model_issue = manifest.pop("_model_issue", None)
            async with self._control_lock:
                if self._terminated:
                    raise TaskError("host_terminated", "the shared host is stopping or terminated")
                if expected_config is not None and self.config != expected_config:
                    raise TaskError("legacy_plan_stale", "插件配置在核对映射期间已变化，请重新试算。")
                root = profile_root / "runtime"
                if self.runtime is not None and (self.runtime.data_root != root.resolve()
                        or self.runtime.installation_id != settings.installation_id):
                    raise TaskError("host_storage_changed", "stop and reload before changing the active V2 data installation")
                self._accepting = False
                session = self.session
            if session is not None:
                report = await session.close(grace_seconds=2)
                if report.state != "stopped":
                    raise TaskError("host_must_stop", "the current session is still stopping")
            async with self._control_lock:
                if self._terminated:
                    raise TaskError("host_terminated", "the shared host is stopping or terminated")
                if expected_config is not None and self.config != expected_config:
                    raise TaskError("legacy_plan_stale", "插件配置在等待保存期间已变化，请重新试算。")
                self._idle()
                root = profile_root / "runtime"
                if self.runtime is not None and (self.runtime.data_root != root.resolve()
                        or self.runtime.installation_id != settings.installation_id):
                    raise TaskError("host_storage_changed", "stop and reload before changing the active V2 data installation")
                # A startup gate refusal is sticky for this process: opening the
                # database here (the only path that bypasses _create_storage) would
                # fork exactly like the startup would have. Require a restart after
                # the journal is fixed.
                if self.restore_blocked:
                    raise TaskError(
                        "backup_restore_startup_blocked",
                        "a staged restore could not be rolled back; fix the data root and restart the plugin")
                opened_here = self.runtime is None
                if self.runtime is None and (root / StorageRuntime.DATABASE_FILENAME).is_file():
                    # This path opens the store without going through
                    # _create_storage. It still must apply a staged restore first
                    # and honor the same rollback gate; otherwise a crash journal
                    # could be bypassed by saving a mapping before the host starts.
                    storage_config = self.config.get("storage", {}) if isinstance(self.config, dict) else {}
                    backup_directory = selected_backup_directory(
                        root, storage_config.get("backup_path", "") if isinstance(storage_config, dict) else "",
                        settings.installation_id, plugin_root=self.data_root)
                    last_restore, restore_state = await asyncio.to_thread(
                        guard_restore_startup, root, installation_id=settings.installation_id,
                        backup_directory=backup_directory)
                    self.last_restore, self.restore_state = last_restore, restore_state
                    if restore_state.get("blocking"):
                        self.restore_blocked = True
                        raise TaskError(restore_state["code"], restore_state["detail"])
                    self.restore_blocked = False
                    self.runtime = await asyncio.to_thread(
                        StorageRuntime.open, data_root=root, installation_id=settings.installation_id)
                    self.runtime.backup_directory = backup_directory
                try:
                    if self.runtime is not None:
                        authority = MultiAuthority(self.runtime, settings, now=self.now,
                            platform_name=self._configured_platform_name)
                        with self.runtime.transaction():
                            authority.initialize(manifest, reviewed=True,
                                                 allow_legacy_staging=adopting_legacy_profile)
                            save(candidate)
                        self.runtime.close()
                        self.runtime = self.authority = None
                    else:
                        save(candidate)
                except Exception:
                    if opened_here and self.runtime is not None:
                        self.runtime.close()
                        self.runtime = self.authority = None
                    raise
                if self.native_extensions is not None:
                    await self.native_extensions.aclose()
                    self.native_extensions = None
                self.session = self.last_session = None
                self.refresh_settings()
                self._accepting = False
                self.last_failure = None
                registered = bool(self.web_api._registered_routes)
                if registered:
                    self.unregister_routes()
                    self.register_routes()
                result = {"saved": True, "requires_reload": False, "requires_initialize": False,
                          "started": False}
        # The old runtime is released before this point.  Re-enter the public
        # lifecycle methods only after the apply lock has been released; both
        # methods acquire the same lifecycle lock themselves.
        if settings.enabled:
            if model_issue is not None:
                result.update(requires_initialize=True, started=False, startup_error=model_issue)
                return result
            initialized = await self.initialize_profile()
            if initialized.failure is not None:
                result.update(requires_initialize=True, started=False,
                              startup_error=initialized.failure.code)
                return result
            started = await self.start()
            if started.failure is not None:
                result.update(started=False, startup_error=started.failure.code)
                return result
            result["started"] = True
        return result

    async def load_existing(self):
        async with self._lifecycle_lock:
            try:
                self.refresh_settings()
                self._settings()
                if not (self.profile_root / "runtime" / StorageRuntime.DATABASE_FILENAME).is_file():
                    return Outcome.success({"loaded": False, "started": False})
                self._idle()
                # Loading verifies the saved account/persona manifest. Window
                # provisioning belongs to start(), after the host is assembled.
                probed, _ = await self._probe(include_conversations=False)
                async with self._control_lock:
                    if self._terminated:
                        raise TaskError("host_terminated", "the shared host is stopping or terminated")
                    self._idle()
                    await self._open_async()
                    try:
                        await asyncio.to_thread(self.authority.require_manifest, probed)
                    except TaskError as error:
                        if error.code != "host_binding_changed":
                            raise
                        saved = self.authority.require_manifest()
                        candidate, repaired = recover_repeated_single_target(self.config, saved.get("binding_contract"))
                        if repaired:
                            probed = self._recover_saved_mapping(candidate, probed)
                        elif self._confirmed_core_window():
                            await asyncio.to_thread(self._adopt_confirmed_mapping, probed)
                        else:
                            raise
                    await self._assemble(probed)
                self.last_failure = None
                return Outcome.success({"loaded": True, "started": False})
            except Exception as error:
                return self._failure(error, operation="load_existing")

    def _recover_saved_mapping(self, candidate, manifest):
        """Restore a migration-generated extra route to the durable reviewed map."""
        settings, root, adopting_legacy_profile = self._resolve_settings(candidate)
        if (root / "runtime" != self.runtime.data_root or settings.installation_id != self.runtime.installation_id):
            raise TaskError("host_storage_changed", "the recovered mapping must keep the existing V2 installation")
        restored = {**manifest, "binding_contract": settings.binding_contract,
                    "settings_fingerprint": settings.fingerprint}
        restored.pop("fingerprint", None)
        restored["fingerprint"] = digest(restored)
        self.authority.require_manifest(restored)
        save = getattr(self, "save_plugin_config", None)
        if not callable(save):
            raise TaskError("account_config_persistence_unavailable", "恢复已保存映射需要可用的插件配置保存接口。")
        try:
            save(candidate)
        except Exception as error:
            raise TaskError("account_config_save_failed", "自动恢复角色映射时保存失败，请检查插件配置文件是否可写。") from error
        self.settings, self.settings_error = settings, None
        self._storage_profile_root, self._adopting_legacy_profile = root, adopting_legacy_profile
        self.authority.settings = settings
        if self.web_api._registered_routes:
            self.unregister_routes()
            self.register_routes()
        logger.info("[CompanionV2] 已移除重复迁移的旧私聊目标，恢复已保存的角色映射")
        return restored

    def _confirmed_core_window(self) -> bool:
        """Whether Dashboard已确认的核心窗口仍在当前映射里。

        只有「开始使用」里确认过、且映射仍然存在的核心管理员窗口才算操作员已复核；
        没有绑定文件时不猜，继续按绑定变化拒绝启动。
        """
        parked = self._parked_binding()
        return bool(parked is not None and parked.confirmed and self._core_target(parked) is not None)

    def _adopt_confirmed_mapping(self, manifest):
        """Re-pin a manifest that only lags behind an already-confirmed core window.

        ``confirm_binding`` 把确认过的窗口写进插件配置，但当时的 manifest 还是
        改动前的映射。此后每次加载都会以 host_binding_changed（private_target /
        persona）拒绝启动，而页面上没有任何操作能修好它。确认过的窗口就是操作员
        的复核结论，这里按 reviewed 重新固定；身份归属真的变了仍由
        host_identity_conflict 拦下。
        """
        with self.runtime.transaction():
            receipt = self.authority.initialize(manifest, reviewed=True,
                                                allow_legacy_staging=self._adopting_legacy_profile)
        logger.info("[CompanionV2] 已按开始使用中确认的核心管理员窗口更新账号与角色绑定")
        return receipt

    async def start(self):
        async with self._lifecycle_lock:
            try:
                if self.session is not None and self.session.application.state == "running":
                    return Outcome.success({"state": "running", "replayed": True})
                self._idle()
                # Model settings can be changed from another workspace while
                # this host is stopped. Re-read them here so retrying Start
                # uses the newly selected provider without re-saving mappings.
                self.refresh_settings()
                self._settings()
                manifest, conversations = await self._probe()
                async with self._control_lock:
                    if self._terminated:
                        raise TaskError("host_terminated", "the shared host is stopping or terminated")
                    self._settings()
                    self._idle()
                    await self._open_async()
                    try:
                        await asyncio.to_thread(self.authority.require_manifest, manifest)
                    except TaskError as error:
                        if error.code == "host_not_initialized":
                            await self._initialize_manifest_locked(manifest, conversations)
                            await asyncio.to_thread(self.authority.require_manifest, manifest)
                        elif error.code == "host_binding_changed" and self._confirmed_core_window():
                            await asyncio.to_thread(self._adopt_confirmed_mapping, manifest)
                        else:
                            raise
                    if self.session is None or self.session.application.state != "new":
                        await self._assemble(manifest)
                    session = self.session
                return await self._start_initialized_session(session)
            except Exception as error:
                self._accepting = False
                return self._failure(error, operation="start")

    async def _start_initialized_session(self, session):
        task, model = await asyncio.to_thread(self._startup_generations)
        result = await session.start(expected_task_generation=task["generation"] if task else 0,
                                     expected_model_generation=model["generation"] if model else 0)
        require(result)
        async with self._control_lock:
            self._settings()
            if self.session is not session:
                raise TaskError("host_session_changed", "the initialized host changed before it could resume")
            self._accepting, self.last_failure = True, None
            targets = len(self.settings.all_targets)
        return Outcome.success({"state": "running", "targets": targets, "startup": result.unwrap()})

    def _startup_generations(self):
        task = self.runtime.read_one("SELECT generation FROM task_supervisor WHERE singleton = 1", ())
        model = self.runtime.read_one("SELECT generation FROM configuration_consumers WHERE consumer_id = ?",
                                      ("model_gateway",))
        return task, model

    def target_for_event(self, event, *, connected=True):
        """The configured target this native event belongs to, if any.

        ``connected=False`` keeps the routing answer and drops the liveness
        probe: an event whose settings still map it to a target stays that
        target's even while the account has no client to send with.  Callers
        that report *why* a reply cannot be delivered need the difference,
        because an unconfigured route and a disconnected account are not the
        same refusal.
        """
        if not isinstance(event, self.native_types.event) or self.settings is None:
            return None
        try:
            message, session, meta = event.message_obj, event.session, event.platform_meta
            if getattr(message.type, "value", None) == "GroupMessage":
                route = group_event_route(event)
                target = self.settings.group_target(route.platform_id, route.account_id, route.group_id)
                platform = self.context.get_platform_inst(meta.id)
                if (target is None or platform is None or platform.get_client() is not event.bot
                        or (connected and not native_client_connected(
                            platform, route.account_id, event.bot))):
                    return None
                return target
            sender, login = event_peer_id(event), event_account_id(event)
            if (message.group_id or getattr(message.type, "value", None) != "FriendMessage"
                    or getattr(session.message_type, "value", None) != "FriendMessage"
                    or not supported_platform_name(meta.name) or session.platform_id != meta.id
                    or session.platform_name != meta.id or session.session_id != sender
                    or event.unified_msg_origin != f"{meta.id}:FriendMessage:{sender}"):
                return None
            target = self.settings.private_target(meta.id, login, sender)
            platform = self.context.get_platform_inst(meta.id)
            if target is None or platform is None or platform.get_client() is not event.bot:
                return None
            return self.settings.effective_private_target(target)
        except (ValueError, AttributeError, TypeError):
            return None

    async def persona_mapping_for_event(self, event):
        """Return the registered character from the installed window snapshot."""
        target = self.target_for_event(event)
        session = self.session
        if target is None or session is None:
            return None
        if isinstance(target, GroupTarget):
            route = group_event_route(event)
            bridge = session.bridge.groups
            native_scope_id = route.session_id
        else:
            bridge = session.bridge
            native_scope_id = target.participant_id
        account = bridge.accounts.get((target.platform_id, target.account_id))
        if account is None or account.bot_id != target.bot_id:
            return None
        umo = bridge._umo(target.platform_id, native_scope_id)
        # A native persona can change without replacing the conversation ID.
        # Refresh once at admission; later steps in this turn reuse its snapshot.
        # An unmapped native persona is a valid non-admission result, rather
        # than a host failure. Keep the event router alive so another mapped
        # window can continue to operate and the operator can repair the
        # mapping from the dashboard.
        try:
            _, persona_id, _, special = await bridge._selected_persona(account, umo, fresh_selection=True)
        except TaskError as error:
            if error.code in {
                "host_persona_unmapped",
                "host_persona_window_unbound",
                "host_persona_unavailable",
                "host_persona_invalid",
                "host_conversation_missing",
            }:
                return None
            raise
        if special or type(persona_id) is not str:
            return None
        return find_persona_mapping(self.settings.personas, target.bot_id, persona_id)

    def claims(self, event):
        from .recalls import is_notice_event
        if is_notice_event(event):
            return False
        return bool(self._accepting and not self._terminated and self.session is not None
                    and self.session.application.state == "running" and self.target_for_event(event) is not None)

    def _delivery_denial(self, event, route):
        """Why a reply that was already admitted cannot be sent now.

        Intake needs one yes/no answer; a written reply needs the same gate
        plus a reason an operator can act on.  A route that no longer maps
        here, a host that stopped, and an account whose native client just
        went away all refuse the send, and one code for all three hides the
        only case that recovers by itself.
        """
        if route is None:
            return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
        if self._terminated:
            return Outcome.failed("host_terminated", "the shared host is stopping or terminated")
        session = self.session
        if session is None or session.application.state != "running":
            return Outcome.failed("host_not_running", "the shared host is not running")
        if not self._accepting:
            return Outcome.failed("host_not_accepting", "the shared host is not accepting replies")
        if self.target_for_event(event) is not None:
            return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
        return Outcome.failed("host_client_offline", "this account's native client is not connected")

    async def receive_notice(self, event):
        from .pokes import is_poke_notice, receive_poke
        if is_poke_notice(event):
            return await receive_poke(self, event)
        from .recalls import receive_notice
        return await receive_notice(self, event)

    async def observe_context_reset(self, event):
        from .conversation_reset import observe_context_reset
        return await observe_context_reset(self, event)

    async def observe_group_early(self, event):
        """Run the V1-style group preflight before reply admission.

        The early hook is deliberately limited to an exact configured,
        authenticated group route.  It only captures the bounded text
        skeleton and materializes the group persona binding; attachment
        preparation, model generation and delivery remain in ``receive``.
        """
        from .group_settings import GroupTarget

        if (not self._accepting or self._terminated or self.session is None
                or self.session.application.state != "running"):
            return Outcome.failed("host_not_claimed", "the shared host is not accepting group input")
        try:
            target = self.target_for_event(event)
        except (TaskError, ValueError, AttributeError, TypeError):
            target = None
        if not isinstance(target, GroupTarget):
            return Outcome.failed("host_target_unavailable", "the native group route is not configured")
        return await self.session.prepare_group(event, target)

    async def process_relay_group_presence(self, event, target, context):
        """Deliver delayed group relays after the sender is authenticated."""
        from .relay_pending_events import process_group_presence
        try:
            await process_group_presence(self, event, target, context)
        except Exception:
            logger.warning("[CompanionV2] 延迟跨群转述处理失败", exc_info=True)

    async def process_private_relay_receipts(self, event, target):
        """Report a target member's private reply to the original relay source."""
        from .relay_pending_events import process_private_receipts
        try:
            await process_private_receipts(self, event, target)
        except Exception:
            logger.warning("[CompanionV2] 私聊递话回执处理失败", exc_info=True)

    async def group_reply_candidate(self, event):
        session, target = self.session, self.target_for_event(event)
        if not self.claims(event) or session is None or not isinstance(target, GroupTarget):
            return Outcome.failed("host_not_claimed", "group reply candidate requires an active configured target")
        return await session.prepare_group(event, target, reply_candidate=True)

    async def receive_group_repeat(self, event, admission, *, prepared=None):
        """Forward the two literal repeat actions without a model fallback."""
        from .group_repeat import receive_group_repeat

        try:
            target = self.target_for_event(event)
            if not self.claims(event) or not isinstance(target, GroupTarget):
                return Outcome.failed("host_not_claimed", "repeat requires an active configured group")
            if not isinstance(admission, dict) or admission.get("reason") != "repeat":
                raise TaskError("group_repeat_admission_required", "a repeat candidate is required")
            if prepared is not None and (
                    prepared.get("target_id") != target.target_id
                    or prepared.get("event_id") != admission.get("event_id")
                    or getattr(prepared.get("context"), "key", None) != admission.get("context_key")):
                raise TaskError("group_admission_changed", "repeat input changed after its decision")
            return Outcome.success(await receive_group_repeat(self, event, target, admission, prepared=prepared))
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.warning("[CompanionV2] 群复读动作未完成 error_type=%s", type(error).__name__)
            return Outcome.failed("group_repeat_failed", "the literal repeat action could not be completed")

    async def prepare_core_reply_context(self, event, target=None, *, group_admission=None):
        try:
            session = self.session
            current_target = self.target_for_event(event)
            if target is not None and target != current_target:
                raise TaskError("host_target_mismatch", "the requested core reply target is not the current native route")
            target = current_target
            if not self.claims(event) or session is None or target is None:
                return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
            return await session.prepare_core_reply_context(
                event, target, group_admission=group_admission)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.error("[CompanionV2] 核心回复上下文解析失败 error_type=%s", type(error).__name__)
            return Outcome.failed("host_core_reply_prepare_failed", "core reply admission could not be prepared")

    async def prepare_core_reply(self, event, target=None, *, group_admission=None, plan=None):
        try:
            session = self.session
            current_target = self.target_for_event(event)
            if target is not None and target != current_target:
                raise TaskError("host_target_mismatch", "the requested core reply target is not the current native route")
            target = current_target
            if not self.claims(event) or session is None or target is None:
                return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
            return await session.prepare_core_reply(
                event, target, group_admission=group_admission, plan=plan)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.error("[CompanionV2] 核心回复准入失败 error_type=%s", type(error).__name__)
            return Outcome.failed("host_core_reply_prepare_failed", "core reply admission could not be prepared")

    async def authorize_core_reply(self, event, prepared_or_target=None, prepared=None, *, target=None):
        try:
            if target is not None:
                requested_target = target
                prepared = prepared if prepared is not None else prepared_or_target
            elif prepared is None:
                prepared, requested_target = prepared_or_target, None
            else:
                requested_target = prepared_or_target
            session = self.session
            # Route identity first, then liveness: the target the caller
            # snapshotted at admission is compared with the configured route,
            # so a dropped client cannot masquerade as a changed target.
            target = self.target_for_event(event, connected=False)
            if target is not None and requested_target is not None and requested_target != target:
                raise TaskError("host_target_mismatch", "the requested core reply target is not the current native route")
            if not self.claims(event) or session is None:
                return self._delivery_denial(event, target)
            return await session.authorize_core_reply(event, target, prepared)
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.error("[CompanionV2] 核心回复发前复核失败 error_type=%s", type(error).__name__)
            return Outcome.failed("host_core_reply_authorize_failed", "core reply authorization failed")

    async def receive_core_command(self, event):
        """Proxy the calendar command route without entering V2 reply generation."""
        try:
            session = self.session
            target = self.target_for_event(event)
            if not self.claims(event) or session is None or target is None:
                return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
            result = await receive_calendar_command(
                self, event, session, target=target if is_private_message(event) else None)
            return result if result is not None else Outcome.success({"handled": False})
        except TaskError as error:
            return Outcome.failed(error.code, str(error))
        except Exception as error:
            logger.error("[CompanionV2] 核心命令处理失败 error_type=%s", type(error).__name__)
            return Outcome.failed("host_core_command_failed", "the core command could not be processed")

    async def refuse_core_reply(self, event, context=None):
        """Send the denial notice that this exact event produced.

        ``context`` must come from the failed preparation of the same event.
        It is deliberately not read from the shared session: two Bot accounts
        can be refused concurrently, and one must never send the other's copy
        or clear its cooldown bookmark.
        """
        session = self.session
        if session is None or context is None or context.scope.kind is not ScopeKind.PRIVATE:
            return None
        return await self._refuse_private(session, event, context)

    async def receive_claimed(self, event, *, on_refusal=None, group_admission=None):
        session, target = self.session, self.target_for_event(event)
        if not self.claims(event) or session is None or target is None:
            return Outcome.failed("host_not_claimed", "the native event is outside the active configured targets")
        try:
            command_result = await receive_calendar_command(self, event, session,
                target=target if is_private_message(event) else None)
            if command_result is not None:
                return command_result
            result = await session.receive(event, target, group_admission=group_admission)
            if result.failure is not None and result.failure.code == "private_access_dialogue_denied":
                # 没有对话权限：按配置回一句（真实投递模式才发）。发送失败不影响"拒绝"本身。
                # The context travels on this result, never through a shared slot.
                receipt = await self._refuse_private(
                    session, event, getattr(result, "refusal", None))
                if callable(on_refusal):
                    on_refusal(receipt)
            return result
        except Exception as error:
            return self._failure(error)

    async def _refuse_private(self, session, event, context):
        """一句话的礼拒；任何失败都只当"没回话"，不改写拒绝结论。

        The caller must pass the context that belongs to this event.  A context
        for another configured route is discarded instead of being delivered
        to the wrong Bot account.
        """
        from .private_notice import notify_refusal

        if context is None or context.scope.kind is not ScopeKind.PRIVATE:
            return None
        try:
            target = self.target_for_event(event)
            if target is None or session.bridge.target_for_scope(context.scope) != target:
                logger.warning("[CompanionV2] 私聊礼拒上下文与当前事件路由不一致，放弃发送")
                return None
            return await notify_refusal(self, session, event, context=context)
        except (TaskError, ValueError, TypeError, AttributeError, KeyError):
            return None

    async def authorize_delivery(self, target_id):
        async with self._control_lock:
            try:
                self._settings()
                if self.authority is None:
                    raise TaskError("host_not_initialized", "initialize the shared installation first")
                self.authority.require_manifest()
                return Outcome.success(self.authority.authorize_delivery(target_id, self.now() + DELIVERY_AUTHORIZATION_TTL))
            except Exception as error:
                return self._failure(error)

    async def _group_delivery_control(self, target_id, *, authorize):
        async with self._control_lock:
            try:
                self._settings()
                if self.authority is None:
                    raise TaskError("host_not_initialized", "initialize the shared installation first")
                self.authority.require_manifest()
                groups = self.authority.group_delivery
                result = (groups.authorize(target_id, self.now() + DELIVERY_AUTHORIZATION_TTL) if authorize
                          else groups.revoke(target_id))
                return Outcome.success(result)
            except Exception as error:
                return self._failure(error)

    async def authorize_group_delivery(self, target_id):
        return await self._group_delivery_control(target_id, authorize=True)

    async def revoke_group_delivery(self, target_id):
        return await self._group_delivery_control(target_id, authorize=False)

    def preview(self, target_id, batch_id):
        try:
            self._settings()
            target = self.settings.target(target_id)
            session = self.session or self.last_session
            if session is None or self.runtime is None:
                raise TaskError("host_not_initialized", "initialize or load the shared installation first")
            if type(batch_id) is not str or not 1 <= len(batch_id) <= 128:
                raise TaskError("host_request_invalid", "select an original input batch")
            messages = session.application.messages
            with session.application.tasks.read_only_context():
                batch = messages.batch(batch_id)
                context = TaskContext.restore(json.loads(batch["context_json"]))
                if session.bridge.target_for_scope(context.scope) != target:
                    raise TaskError("host_target_mismatch", "the batch belongs to another target")
                batch = messages.owned(context, batch_id)
                if batch["state"] in ("cancelled", "invalidated"):
                    raise TaskError("staging_draft_unavailable", "the original draft has been invalidated")
                request = messages._request(batch, context, self.now()) if batch["state"] == "ready" else None
                parts = [dict(row) for row in self.runtime.read_all(
                    "SELECT part_id, sequence, text, state, operation_id, receipt_ref, platform_ref, delivered_at "
                    "FROM conversation_parts WHERE batch_id = ? ORDER BY sequence", (batch_id,))]
                if batch["state"] == "ready":
                    parts = messages.reply_images.project_parts(parts, batch, context, self.now(), request=request)
                return Outcome.success({"target_id": target_id, "batch_id": batch_id, "scope_kind": context.scope.kind.value,
                    "role_id": context.role_binding.role.subject_id, "speaker_id": context.actor.principal.native_account_id,
                    "delivery_mode": (self.authority.group_delivery.mode(batch_id) if context.scope.kind is ScopeKind.GROUP
                                      else self.settings.common.delivery_mode),
                    "configured_delivery_mode": self.settings.delivery_mode_for(target),
                    "preview_ready": bool(parts), "sent": any(p["state"] == "delivered" for p in parts), "parts": parts})
        except Exception as error:
            return self._failure(error)

    def batches(self, target_id):
        try:
            self._settings()
            target = self.settings.target(target_id)
            if self.runtime is None:
                raise TaskError("host_not_initialized", "initialize or load the shared installation first")
            group = isinstance(target, GroupTarget)
            rows = self.runtime.read_all("""SELECT batch_id, state, created_at, part_count, context_json
                FROM conversation_batches WHERE json_extract(context_json, '$.audience.scope.platform') = ?
                AND json_extract(context_json, '$.audience.scope.account_id') = ?
                AND json_extract(context_json, '$.audience.scope.bot_id') = ?
                AND json_extract(context_json, '$.audience.scope.kind') = ?
                AND json_extract(context_json, '$.audience.scope.native_scope_id') = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 21""", (target.platform_id, target.account_id, target.bot_id,
                    "group" if group else "private", target.group_id if group else target.participant_id))
            items = []
            for row in rows[:20]:
                context = TaskContext.restore(json.loads(row["context_json"]))
                items.append({**{name: row[name] for name in ("batch_id", "state", "created_at", "part_count")},
                    "role_id": context.role_binding.role.subject_id, "speaker_id": context.actor.principal.native_account_id})
            return Outcome.success({"target_id": target_id, "items": items, "has_older": len(rows) > 20})
        except Exception as error:
            return self._failure(error)

    async def revoke_delivery(self, target_id):
        async with self._control_lock:
            try:
                self._settings()
                if self.authority is None:
                    raise TaskError("host_not_initialized", "initialize the shared installation first")
                return Outcome.success(self.authority.revoke_delivery(target_id))
            except Exception as error:
                return self._failure(error)

    async def stop(self, *, grace_seconds=0.5):
        self._accepting = False
        async with self._lifecycle_lock:
            self._accepting = False
            try:
                async with self._control_lock:
                    session = self.session
                if session is None:
                    return Outcome.success({"state": "stopped", "pending_handles": []})
                report = await session.close(grace_seconds=grace_seconds)
                async with self._control_lock:
                    if report.state == "stopped" and self.session is session:
                        self.last_session, self.session = session, None
                return Outcome.success(report)
            except Exception as error:
                return self._failure(error)

    def status(self):
        session = self.session or self.last_session
        return {"mode": "multi", "enabled": self.settings.enabled if self.settings else False,
                "installation_id": self.settings.installation_id if self.settings else None,
                # 保留在状态文档中，方便旧版小屋和诊断页识别当前群目标形态。
                # 正式版的 onebot_group 只是配置描述，不是额外的投递授权闸门。
                "group_delivery_mode": (
                    self.settings.group_delivery_mode if self.settings else None
                ),
                "state": session.application.state if session else "uninitialized",
                "accepting": self._accepting and session is not None and session.application.state == "running",
                "failure_code": session.application.failure_code if session else None,
                "failure_detail": session.application.failure_detail if session else None,
                "targets": self.web_api.target_inventory(), "last_failure": self.last_failure,
                # Why the store was or was not opened. ``restore_state.blocking`` is
                # the startup gate; the detail names the journal and never contains
                # credentials or snapshot bytes.
                "restore_state": self.restore_state,
                "settings_error": getattr(self.settings_error, "code", None),
                "parser_availability": parser_availability()}

    def register_routes(self):
        self.web_api.register()

    def unregister_routes(self):
        self.web_api.unregister()

    async def terminate(self):
        self._terminated, self._accepting = True, False
        if self._termination_task is not None:
            return
        try:
            await self.session_topology.close()
            result = await self.stop(grace_seconds=2)
            if result.is_success:
                report = result.unwrap()
                state = report.get("state") if isinstance(report, dict) else report.state
            else:
                state = None
            if state != "stopped" and self.session is not None:
                logger.warning("[CompanionV2] 工作任务仍在退出，保留存储直到停止证明写入完成")
                self._termination_task = asyncio.create_task(self._finish_termination(),
                    name="companion:host-drain")
            else:
                await self._release_runtime()
        finally:
            self.unregister_routes()

    async def _release_runtime(self):
        async with self._control_lock:
            if self.runtime is not None:
                self.runtime.close()
                self.runtime = self.authority = None
            if self.native_extensions is not None:
                await self.native_extensions.aclose()
                self.native_extensions = None

    async def _finish_termination(self):
        try:
            while True:
                report = await self.session.close(grace_seconds=0)
                if report.state == "stopped":
                    self.last_session, self.session = self.session, None
                    await self._release_runtime()
                    logger.info("[CompanionV2] 延迟停机完成，工作任务停止证明已保存")
                    return
                await asyncio.sleep(0.25)
        except Exception as error:
            self._failure(error, operation="terminate_drain")
            self._termination_task = None
