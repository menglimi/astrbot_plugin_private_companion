"""Own the pinned profile, authorization, intake and bounded staging lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from companion_v2.adapters.astrbot.native import event_account_id, event_peer_id, native_id, supported_platform_name
from companion_v2.adapters.astrbot.staging_bootstrap import AstrBotStagingBootstrap
from companion_v2.adapters.astrbot.staging_config import StagingConfiguration, StagingConfigurationError
from companion_v2.capabilities import CapabilityManagementService
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.conversation.pokes import PokePolicy
from companion_v2.capabilities.screen_access import PACKAGE_ID as SCREEN_PACKAGE_ID
from companion_v2.screen import SCREEN_EXTENSION, SCREEN_PROCESS
from .integration_registry import IntegrationRegistry
from companion_v2.model_gateway.routing import CONSUMER
from companion_v2.storage.runtime import StorageRuntime
from companion_v2.proactive.search_settings import SearchSettingsStore

from .native import NativeProbe, NativeProbeService, installed_native_types
from .profile_residency import profile_residency
from .native_extensions import NativeExtensionRegistry, together_execution_binding
from .native_extensions.builtins import MANAGED_EXTENSIONS, create_bundled_extensions
from .backup_api import apply_staged_restore
from .storage_location import selected_backup_directory
from .binding import (
    describe_pin, event_identity, is_private_message, message_text, parse_bind_command,
    refuse_already_bound,
)
from .calendar_command import receive_calendar_command
from .binding_store import PendingBinding, binding_path, clear_binding, read_binding, write_binding
from .management_identity import dashboard_operator
from .settings import HostControlError, HostSettings
from .voice import NativeVoiceReply
from .weather_settings import configured_weather_ports
from .files.parsing import parser_availability
from .service_balance import configured_balance_port
from .news import configured_news_port, configured_search_port
from .solar_terms import configured_solar_term_port
from .qzone_client import QzoneHttpClient
from .qzone_transport import build_qzone_transport
from .web_api import StagingWebAPI
from .recalls import is_notice_event, receive_notice
from .pokes import is_poke_notice, receive_poke
from .session_topology import SessionTopology


logger = logging.getLogger(__name__)

# `StagingConfigurationError` reasons are bounded, secret-free sentences such as
# "root: missing, unknown or inapplicable fields". Classify them into a stable
# suffix so the operator learns *which* repair is needed without the raw reason
# leaking into the UI. Unknown shapes fall back to a generic suffix rather than
# echoing the text: the reason may still quote a field name.
_INTENT_REASON_SUFFIXES = (
    ("without BOM", "__bom"),
    ("exceeds the UTF-8 document limit", "__too_large"),
    ("unsupported staging configuration version", "__version"),
    ("duplicate fields", "__duplicate"),
    ("non-finite", "__not_finite"),
    ("missing, unknown or inapplicable fields", "__schema"),
    ("requires one complete private target", "__scope"),
    ("expected a SHA-256 fingerprint", "__fingerprint"),
    ("must be finite", "__not_finite"),
    ("not valid JSON", "__not_json"),
)


def _intent_reason_suffix(error: BaseException) -> str:
    if isinstance(error, OSError):
        return "__unreadable"
    if isinstance(error, UnicodeError):
        return "__not_utf8"
    reason = str(error)
    for needle, suffix in _INTENT_REASON_SUFFIXES:
        if needle.lower() in reason.lower():
            return suffix
    return "__invalid"


class AstrBotStagingController:
    """Own one fixed staging profile and its authenticated management surface."""

    def __init__(self, context: object, config: object, data_root: Path, *, native_types=None, now=None):
        self.lifecycle_token = uuid4().hex
        self.context = context
        self.config = config
        self.data_root = Path(data_root).resolve()
        self.native_types = native_types or installed_native_types()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.runtime: StorageRuntime | None = None
        self.bootstrap: AstrBotStagingBootstrap | None = None
        self.configuration: StagingConfiguration | None = None
        self.session = None
        self.last_session = None
        self.latest_batch_id: str | None = None
        self.last_failure: dict[str, str] | None = None
        # The result of applying a staged restore at assembly time, kept so the
        # dashboard can report that a restart actually consumed the request.
        self.last_restore: dict[str, Any] | None = None
        self._accepting = False
        self._terminated = False
        # Keep native lifecycle waits separate from the lock shared by data APIs.
        self._control_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self.session_topology = SessionTopology(self)
        self.native_extensions = None
        # The QQ Zone extension only exists once a host transport is attached; it
        # is created lazily so an unused Zone never opens a session or reads a cookie.
        self._qzone_client = None
        # The product manifest is assembled from V2 core modules and public
        # extension APIs. It does not read another plugin's files or objects.
        self.integration = IntegrationRegistry(context)
        self.native = NativeProbeService(context, self.native_types, self.now)
        self.web_api = StagingWebAPI(self)
        self.poke_policy = PokePolicy()
        try:
            self.settings = HostSettings.parse(config)
            self.poke_policy = PokePolicy.parse(config.get("poke"))
            self.settings_error: HostControlError | None = None
        except (HostControlError, ValueError) as error:
            self.settings = None
            self.settings_error = (error if isinstance(error, HostControlError) else
                                   HostControlError("host_poke_settings_invalid", str(error)))

    def _parsed_settings(self) -> HostSettings:
        if self._terminated:
            raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
        if self.settings_error is not None:
            raise self.settings_error
        assert self.settings is not None
        return self.settings

    def refresh_settings(self) -> None:
        """Apply the in-memory AstrBot config after an administrator saves it."""
        try:
            self.settings = HostSettings.parse(self.config)
            self.poke_policy = PokePolicy.parse(self.config.get("poke"))
            self.settings_error = None
        except (HostControlError, ValueError) as error:
            self.settings = None
            self.settings_error = (error if isinstance(error, HostControlError) else
                                   HostControlError("host_poke_settings_invalid", str(error)))

    def _settings(self) -> HostSettings:
        settings = self._parsed_settings()
        settings.require_ready()
        return settings

    def resolved_settings(self) -> HostSettings:
        """Settings carrying whatever conversation target is currently known.

        The target no longer comes from the plugin config, so it has to be looked up
        from one of two places, in this order:

        1. **The pinned profile.** Once ``staging-config.json`` exists it is the
           authority -- it records the target the operator actually confirmed, and
           reading it is what keeps a restart on the same conversation.
        2. **The parked binding.** Written by the bind command before anything is
           pinned; it is the operator's stated intent, not yet a commitment.

        With neither, the settings come back as configured and ``has_target`` is
        False, which is the first-run state the setup guide renders. ``probe`` and
        the delivery path both go through here so they cannot disagree about which
        conversation is bound -- a disagreement would look like a profile conflict.
        """
        return self._with_bound_identity(self._parsed_settings())

    def _with_bound_identity(self, settings: HostSettings) -> HostSettings:
        """Resolve the same binding for lifecycle operations and read-only status."""
        if self._multi_host:
            return settings
        pinned = self._read_intent()
        if pinned is not None:
            return settings.with_identity({
                "platform_id": pinned.scope.platform,
                "account_id": pinned.scope.account_id or "",
                "participant_id": pinned.scope.native_scope_id,
                "bot_id": pinned.scope.bot_id or "",
                "role_id": pinned.persona.role.subject_id,
                "persona_id": pinned.persona.persona_id or "",
            })
        parked = self._parked_binding()
        if parked is not None and parked.confirmed:
            try:
                return settings.with_identity(parked.identity())
            except HostControlError:
                # A parked binding whose account no longer validates is treated as absent:
                # the operator rebinds, which is the same remedy as any other first-run
                # mistake, and it must not take the whole host down.
                pass
        # A manually completed target is still a valid first-run input. Its logical
        # bot and role must use the same deterministic derivation as a chat binding;
        # otherwise the first reload would silently switch memory ownership when the
        # schema defaults (staging-bot/staging-role) are present.
        if settings.has_target:
            try:
                from .identity_derivation import derived_identity

                return settings.with_identity(derived_identity(
                    platform_id=settings.platform_id,
                    account_id=settings.account_id,
                    participant_id=settings.participant_id,
                    persona_id=settings.persona_id,
                ))
            except HostControlError:
                return settings
        return settings

    @property
    def _multi_host(self) -> bool:
        """Whether the active controller administers several targets.

        Declared here as False so the single-target controller can ask the question
        without importing the multi-target one, which would be a cycle.
        """
        return False

    @property
    def profile_root(self) -> Path | None:
        if self.settings is None:
            return None
        return self.data_root / "profiles" / self.settings.profile_id

    @property
    def binding_root(self) -> Path:
        """Stable setup metadata shared by single and multi-target modes."""
        return self.data_root / "setup"

    def _parked_binding(self) -> PendingBinding | None:
        path = binding_path(self.binding_root)
        if path.exists():
            return read_binding(self.binding_root)
        # Read records written by the previous V2 setup implementation.
        root = self.profile_root
        return read_binding(root) if root is not None else None

    @property
    def intent_path(self) -> Path | None:
        root = self.profile_root
        return root / "staging-config.json" if root is not None else None

    def _read_intent(self) -> StagingConfiguration | None:
        path = self.intent_path
        if path is None or not path.exists():
            return None
        try:
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raise StagingConfigurationError("configuration must be UTF-8 without BOM")
            return StagingConfiguration.parse(raw.decode("utf-8"))
        except (OSError, UnicodeError, StagingConfigurationError) as error:
            # A pinned profile that cannot be parsed blocks probing, so the operator
            # needs to know *which* way it is unreadable. `StagingConfigurationError`
            # carries a bounded, secret-free reason ("root: missing, unknown or
            # inapplicable fields"); classify it into a stable suffix that travels as
            # part of the code, and keep the original text beside it for the log.
            raise HostControlError("staging_intent_invalid" + _intent_reason_suffix(error),
                                   "the pinned staging configuration is unreadable or invalid") from error

    def _write_intent(self, configuration: StagingConfiguration) -> None:
        path = self.intent_path
        if path is None:
            raise HostControlError("staging_settings_invalid", "a profile directory could not be resolved")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
        payload = json.dumps(
            configuration.document(), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2
        ) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise HostControlError("staging_intent_write_failed", "the pinned staging configuration could not be stored") from error
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _profile_conflict(current, pinned) -> HostControlError:
        """Name the fields that moved, never their values.

        The operator sees only "something differs" otherwise, which cannot be acted
        on: they cannot tell whether the target, persona, provider or a limit moved.
        """
        labels = {
            "scope": "平台、Bot 账号或私聊对象",
            "subject": "私聊对象身份",
            "persona": "AstrBot 人格",
        }
        changed = "、".join(labels.get(name, name) for name in current.rebinding_differences(pinned)) or "未知绑定项"
        return HostControlError(
            "staging_profile_conflict",
            f"固定运行档的绑定身份发生变化，请检查：{changed}。模型参数变化不会阻止连接。",
        )

    def _check_contract(self, current, pinned) -> None:
        """Refuse only a rebinding; let environment drift through with a warning.

        A profile pinned before a provider was renamed upstream, a re-tuned budget, or a
        field AstrBot added in an upgrade must still load -- none of those repoint the
        role. Refusing them took an entire installation offline for a change nobody made
        here, and the operator could only see "connection failed".
        """
        if pinned is None or current.same_contract(pinned):
            return
        if current.rebinding_differences(pinned):
            raise self._profile_conflict(current, pinned)
        drifted = ", ".join(current.contract_differences(pinned)) or "unknown"
        logger.warning(
            "[CompanionV2] 固定 profile 与当前宿主存在环境差异，按当前值继续加载：%s", drifted
        )

    @staticmethod
    def _same_binding(current, pinned) -> bool:
        """Whether the live probe still points at the pinned identity.

        Persona instruction text and the platform-created conversation id are
        environment details. They must not make an already bound private chat
        unusable after an AstrBot restart or persona edit.
        """
        return not current.rebinding_differences(pinned)


    @staticmethod
    def _failure(error: Exception, fallback: str) -> Outcome:
        if isinstance(error, (HostControlError, TaskError)):
            return Outcome.failed(error.code, str(error))
        if isinstance(error, StagingConfigurationError):
            return Outcome.failed("staging_configuration_invalid", str(error))
        logger.warning("[CompanionV2] %s", fallback, exc_info=True)
        return Outcome.failed(fallback, "the AstrBot staging control operation failed")

    def _record_failure(self, result: Outcome) -> None:
        if result.failure is not None:
            self.last_failure = {"code": result.failure.code, "reason": result.failure.reason}
        else:
            self.last_failure = None

    def _residency(self, settings) -> dict | None:
        """Whether the selected profile holds this target's data, and who does if not.

        A store is per-profile, so pointing ``staging.profile_id`` at a new name opens an
        empty one: the role reads as if it had forgotten everything while the old store
        sits untouched on disk. Nothing is lost and nothing said so, which is the one
        impression this project must never give. Reported, never enforced.
        """
        participant = getattr(settings, "participant_id", None)
        target = (getattr(settings, "platform_id", "") or "",
                  getattr(settings, "account_id", "") or "", participant or "")
        if not target[1] or not target[2]:
            return None
        return profile_residency(self.data_root, profile_id=settings.profile_id, target=target)

    async def probe(self) -> Outcome[dict[str, Any]]:
        async with self._lifecycle_lock:
            try:
                settings = self.resolved_settings()
                discovery = self.native.discover(settings)
                pinned = self._read_intent()
                residency = self._residency(settings)
                if not discovery["target_ready"]:
                    result = Outcome.success({**discovery, "pinned": pinned is not None,
                                              "profile_residency": residency})
                    self._record_failure(result)
                    return result
                probe = await self._probe_native(
                    valid_from=pinned.valid_from if pinned else None,
                    require_enabled=False,
                )
                # Compared by value, not by hash: a profile pinned by an older writer
                # stores a different document shape for the same settings.
                if pinned is not None and not self._same_binding(probe.configuration, pinned):
                    self._check_contract(probe.configuration, pinned)
                result = Outcome.success({**discovery, **probe.report(pinned=pinned is not None),
                                          "profile_residency": residency})
            except Exception as error:
                result = self._failure(error, "staging_probe_failed")
            self._record_failure(result)
            return result

    async def _probe_native(
        self, *, valid_from: datetime | None = None, require_enabled: bool = True
    ) -> NativeProbe:
        return await self.native.probe(
            self.resolved_settings(), valid_from=valid_from, require_enabled=require_enabled
        )

    def _ensure_assembly(self, configuration: StagingConfiguration) -> None:
        if self.configuration is not None:
            # Same rule as everywhere else: only a rebinding closes an already-open
            # runtime. Environment drift reuses the open assembly instead of refusing.
            if self.configuration.rebinding_differences(configuration):
                raise HostControlError("staging_profile_conflict", "the open runtime belongs to another staging profile")
            return
        root = self.profile_root
        if root is None:
            raise HostControlError("staging_settings_invalid", "a profile directory could not be resolved")
        # A staged restore is applied here, before the store is opened: this is the
        # only moment the database file can be replaced without orphaning the live
        # connection that every repository and task borrows. The check reads only
        # files, never a runtime this method has not created yet.
        storage = self.config.get("storage", {}) if isinstance(self.config, dict) else {}
        backup_directory = selected_backup_directory(
            root / "runtime", storage.get("backup_path", "") if isinstance(storage, dict) else "",
            configuration.scope.installation_id, plugin_root=self.data_root)
        self.last_restore = apply_staged_restore(root / "runtime",
                                                 installation_id=configuration.scope.installation_id,
                                                 backup_directory=backup_directory)
        runtime = StorageRuntime.open(data_root=root / "runtime", installation_id=configuration.scope.installation_id)
        runtime.backup_directory = backup_directory
        try:
            from companion_v2.expression.configuration import migrate_legacy_policy_values
            from companion_v2.expression.reaction_configuration import migrate_window_values
            from companion_v2.role_work.growth.configuration import migrate_legacy_skill_values
            migrate_window_values(runtime)
            migrate_legacy_policy_values(runtime)
            migrate_legacy_skill_values(runtime)
            bootstrap = AstrBotStagingBootstrap(
                runtime,
                self.context,
                configuration=configuration,
                native_types=self.native_types,
                now=self.now,
            )
        except BaseException:
            runtime.close()
            raise
        self.runtime = runtime
        self.bootstrap = bootstrap
        self.configuration = configuration

    def _qzone_transport(self):
        if self._qzone_client is None:
            self._qzone_client = build_qzone_transport(self.context, data_root=getattr(self, "data_root", None))
        return self._qzone_client

    async def _load_session(self, *, review_only=False) -> object:
        if self.bootstrap is None:
            raise HostControlError("staging_not_initialized", "initialize the staging profile first")
        if self.native_extensions is not None:
            await self.native_extensions.aclose()
        bundled_extensions, bundled_issues = create_bundled_extensions(self.runtime.data_root, now=self.now, context=self.context, controller=self)
        self.native_extensions = NativeExtensionRegistry(self.context, now=self.now,
            together_execution=together_execution_binding(self.settings),
            image_settings_path=self.data_root / "model_image_routes.json",
            bundled_extensions=bundled_extensions, bundled_issues=bundled_issues,
            managed_extensions=MANAGED_EXTENSIONS)
        weather_port, weather_alert_port = configured_weather_ports(self.config)
        from .model_settings import conversation_provider_resolver
        from .relay_tools import RelayToolPort
        search_provider_resolver = SearchSettingsStore(self.data_root).selected_provider_id
        def search_port_factory(context, query, result_count):
            return configured_search_port(self.context, query=query, result_count=result_count,
                task_context=context, topic="general",
                deepseek_provider_resolver=search_provider_resolver)
        loaded = self.bootstrap.load(runtime_instance_id="astrbot-staging-" + uuid4().hex,
                                     review_only=review_only,
                                     native_tool_port=self.native_extensions,
                                     default_provider_resolver=conversation_provider_resolver(self.context),
                                     weather_port=weather_port,
                                     weather_alert_port=weather_alert_port,
                                     balance_port=configured_balance_port(self.config),
                                     news_port=configured_news_port(self.config, context=self.context,
                                         deepseek_provider_resolver=search_provider_resolver),
                                     search_port_factory=search_port_factory,
                                     qzone_transport=self._qzone_transport(),
                                     solar_term_port=configured_solar_term_port(
                                         self.config.get("solar_terms") if isinstance(self.config, dict) else None),
                                     voice=NativeVoiceReply(self.context).render,
                                     additional_providers=self.settings.additional_providers,
                                     fallback_provider_id=self.settings.fallback_provider_id,
                                     replacement_rules=self.settings.replacement_rules,
                                     fallback_on_refusal=self.settings.fallback_on_refusal,
                                     voice_policy=self.settings.voice_reply_policy
                                         if self.settings.voice_reply_enabled else None,
                                     relay_tool_port_factory=lambda bridge: RelayToolPort(
                                         bridge, native_types=self.native_types, enabled=not review_only))
        if loaded.failure is not None:
            await self.native_extensions.aclose()
            raise TaskError(loaded.failure.code, loaded.failure.reason)
        self.session = loaded.unwrap()
        # 关键词材料：服务余额、当前模型与链接正文；读不到时各自判定不可用，登记本身不拦启动。
        try:
            from .service_balance import balance_keyword_material
            from .model_route_material import model_route_keyword_material
            from .web_read_material import web_read_keyword_material
            from companion_v2.conversation.keyword_materials import KeywordMaterials
            async def deepseek_fetch(url):
                port = configured_search_port(self.context, query="链接正文", result_count=1,
                    topic="general", deepseek_provider_resolver=search_provider_resolver)
                if port is None or not callable(getattr(port, "fetch_article", None)):
                    return None
                return await port.fetch_article(url)
            self.session.application.keyword_materials = KeywordMaterials((
                balance_keyword_material(self.session.application),
                model_route_keyword_material(self.session.application, self.context,
                                             native_tool_port=self.native_extensions),
                web_read_keyword_material(self.session.application, fetch=deepseek_fetch),
            ))
        except Exception as error:
            logger.warning("[CompanionV2] 关键词材料登记失败 error_type=%s", type(error).__name__)
        try:
            from .files.parsing import report_parser_availability
            report_parser_availability(logger)
        except Exception as error:
            logger.warning("[CompanionV2] 文档解析组件自检失败 error_type=%s", type(error).__name__)
        try:
            from .image_access import ensure_image_access
            ensure_image_access(self.runtime, self.session.profile.target, observed_at=self.now())
        except Exception as error:
            logger.warning("[CompanionV2] 生图能力授权补齐失败 error_type=%s", type(error).__name__)
        from .knowledge_sources import attach_official_knowledge, import_configured_sources_at_startup
        attach_official_knowledge(self.session.application, self.context)
        # 配置的世界书材料在启动时刷新一次；来源缺失或损坏只记日志，不拦启动，重复刷新按内容去重。
        try:
            import_configured_sources_at_startup(self.session.application.runtime, self.context,
                                                 at=self.now())
        except Exception as error:
            logger.warning("[CompanionV2] 启动知识导入失败 error_type=%s", type(error).__name__)
        self.session.bridge.forward_intake = self.settings.forward_intake
        from .semantic_models import NativeSemanticModels, configured_slang_search_port
        self.session.application.attach_semantics(NativeSemanticModels(self.context, self.settings.provider_id,
                                                                     configured_slang_search_port(self.context,
                                                                         deepseek_provider_resolver=search_provider_resolver)),
                                                  reaction_role_count=1)
        from companion_v2.decision import LayaDecisionService, LayaSettingsStore
        from .laya_client import LayaDecisionClient
        def laya_for_context(context):
            return LayaDecisionService(LayaSettingsStore(self.data_root),
                runtime=self.session.application.runtime, origin_key=context.key, client_factory=LayaDecisionClient)

        self.session.application.attach_laya_decision(laya_for_context)
        return self.session

    async def load_existing(self) -> Outcome[dict[str, Any]]:
        """Read an existing pinned profile during plugin startup; never initialize or start it."""
        async with self._lifecycle_lock:
            try:
                settings = self._settings()
                pinned = self._read_intent()
                if pinned is None:
                    return Outcome.success({"loaded": False, "reason": "not_initialized"})
                probe = await self._probe_native(valid_from=pinned.valid_from)
                if not self._same_binding(probe.configuration, pinned):
                    self._check_contract(probe.configuration, pinned)
                async with self._control_lock:
                    self._ensure_assembly(probe.configuration)
                    await self._load_session()
                self.last_failure = None
                return Outcome.success(
                    {
                        "loaded": True,
                        "profile_id": settings.profile_id,
                        "configuration_fingerprint": pinned.fingerprint,
                        "started": False,
                    }
                )
            except Exception as error:
                result = self._failure(error, "staging_load_failed")
                self._record_failure(result)
                return result

    async def initialize_profile(self) -> Outcome[dict[str, Any]]:
        async with self._lifecycle_lock:
            return await self._initialize_profile_locked()

    async def _initialize_profile_locked(self) -> Outcome[dict[str, Any]]:
        try:
            pinned = self._read_intent()
            parked = self._parked_binding()
            if pinned is None and parked is not None and not parked.confirmed:
                raise HostControlError("setup_binding_confirmation_required",
                                       "confirm the administrator window in the setup guide first")
            probe = await self._probe_native(valid_from=pinned.valid_from if pinned else None)
            configuration = probe.configuration
            if pinned is not None and not self._same_binding(configuration, pinned):
                self._check_contract(configuration, pinned)
            async with self._control_lock:
                if self._terminated:
                    raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                if pinned is None:
                    self._write_intent(configuration)
                    pinned = configuration
                self._ensure_assembly(configuration)
                bootstrap = self.bootstrap
            assert bootstrap is not None
            initialized = await bootstrap.initialize()
            if initialized.failure is not None:
                raise TaskError(initialized.failure.code, initialized.failure.reason)
            async with self._control_lock:
                if self._terminated:
                    raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                if self.session is None or self.session.application.state == "stopped":
                    await self._load_session()
            receipt = initialized.unwrap()
            result = Outcome.success(
                {
                    "initialized": True,
                    "started": False,
                    "replayed": receipt.replayed,
                    "operation_id": receipt.operation_id,
                    "configuration_fingerprint": receipt.configuration_fingerprint,
                    "probe": probe.report(pinned=True),
                }
            )
        except Exception as error:
            result = self._failure(error, "staging_initialize_failed")
        self._record_failure(result)
        return result

    def _generations(self) -> tuple[int, int]:
        if self.runtime is None:
            raise HostControlError("staging_not_initialized", "initialize the staging profile first")
        task = self.runtime.read_one("SELECT generation FROM task_supervisor WHERE singleton = 1", ())
        model = self.runtime.read_one(
            "SELECT generation FROM configuration_consumers WHERE consumer_id = ?", (CONSUMER,)
        )
        return (task["generation"] if task else 0, model["generation"] if model else 0)

    async def start(self, *, review_only=False) -> Outcome[dict[str, Any]]:
        async with self._lifecycle_lock:
            try:
                pinned = self._read_intent()
                if pinned is None:
                    initialized = await self._initialize_profile_locked()
                    if initialized.failure is not None:
                        self._accepting = False
                        return initialized
                    pinned = self._read_intent()
                    if pinned is None:
                        raise HostControlError("staging_not_initialized", "the runtime profile was not created")
                probe = await self._probe_native(valid_from=pinned.valid_from)
                if not self._same_binding(probe.configuration, pinned):
                    self._check_contract(probe.configuration, pinned)
                async with self._control_lock:
                    if self._terminated:
                        raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                    self._ensure_assembly(probe.configuration)
                    session = self.session
                local_review = bool(review_only)
                if session is not None and session.review_only != local_review:
                    if session.application.state == "running":
                        raise HostControlError("staging_session_mode_conflict",
                                               "stop the current session before changing its delivery mode")
                    closed = await session.close(grace_seconds=0.5)
                    if closed.state != "stopped":
                        raise HostControlError("staging_session_still_stopping",
                                               "wait for the previous session to stop before reconnecting")
                    async with self._control_lock:
                        self.last_session = session
                        if self.session is session:
                            self.session = None
                        session = None
                if session is None or session.application.state == "stopped":
                    async with self._control_lock:
                        if self._terminated:
                            raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                        session = await self._load_session(review_only=local_review)
                if session.application.state == "running":
                    session.require_intake_authorization()
                    self._accepting = True
                    result = Outcome.success(
                        {"started": True, "replayed": True, "state": "running", "report": session.inspect().unwrap()}
                    )
                else:
                    async with self._control_lock:
                        task_generation, model_generation = self._generations()
                    started = await session.start(
                        expected_task_generation=task_generation,
                        expected_model_generation=model_generation,
                    )
                    if started.failure is not None:
                        raise TaskError(started.failure.code, started.failure.reason)
                    async with self._control_lock:
                        if self._terminated:
                            raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                        self._accepting = True
                        if review_only:
                            self.latest_batch_id = None
                    result = Outcome.success(
                        {
                            "started": True,
                            "replayed": False,
                            "state": session.application.state,
                            "startup": started.unwrap(),
                            "report": session.inspect().unwrap(),
                        }
                    )
            except Exception as error:
                self._accepting = False
                result = self._failure(error, "staging_start_failed")
            self._record_failure(result)
            return result

    async def reconnect_native(self) -> Outcome[dict[str, Any]]:
        """Reassemble the V2 session so newly loaded native tools are discovered."""
        async with self._lifecycle_lock:
            try:
                previous = self.session or self.last_session
                if previous is None:
                    raise HostControlError("staging_not_initialized", "load the V2 profile first")
                self._accepting = False
                if previous.application.state != "stopped":
                    report = await previous.close(grace_seconds=0.5)
                    if report.state != "stopped":
                        raise HostControlError("staging_session_still_stopping",
                                               "wait for the current V2 session to stop before reconnecting")
                async with self._control_lock:
                    if self._terminated:
                        raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                    self.last_session = previous
                    if self.session is previous:
                        self.session = None
                    session = await self._load_session(review_only=previous.review_only)
                    task_generation, model_generation = self._generations()
                started = await session.start(expected_task_generation=task_generation,
                                              expected_model_generation=model_generation)
                if started.failure is not None:
                    raise TaskError(started.failure.code, started.failure.reason)
                async with self._control_lock:
                    if self._terminated:
                        raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
                    self._accepting = True
                result = Outcome.success({"reconnected": True, "state": session.application.state,
                                          "report": session.inspect().unwrap()})
            except Exception as error:
                self._accepting = False
                result = self._failure(error, "staging_native_reconnect_failed")
            self._record_failure(result)
            return result

    async def authorize_delivery(self, *, window: timedelta | None = None) -> Outcome[dict[str, Any]]:
        """Compatibility response for clients from releases that exposed grants."""
        return Outcome.success({"authorized": True, "required": False, "expires_at": None,
                               "state": "not_required", "replayed": True})

    async def prepare_screen(self) -> Outcome[dict[str, Any]]:
        """Prepare one loaded native Screen declaration without starting intake."""
        async with self._lifecycle_lock:
            try:
                settings, descriptor = await self._screen_declaration()
                assert self.bootstrap is not None
                prepared = self.bootstrap.prepare_screen(descriptor.package)
                if prepared.failure is not None:
                    raise TaskError(prepared.failure.code, prepared.failure.reason)
                receipt = prepared.unwrap()
                result = Outcome.success({"prepared": True, "profile_id": settings.profile_id,
                    "package_version": receipt.package_version, "artifact_hash": receipt.artifact_hash,
                    "replayed": receipt.replayed, "started": False})
            except Exception as error:
                result = self._failure(error, "staging_screen_preparation_failed")
            self._record_failure(result)
            return result

    async def _screen_declaration(self):
        settings = self._settings()
        pinned = self._read_intent()
        if pinned is None:
            raise HostControlError("staging_not_initialized", "initialize the staging profile first")
        probe = await self._probe_native(valid_from=pinned.valid_from)
        if not self._same_binding(probe.configuration, pinned):
            self._check_contract(probe.configuration, pinned)
        async with self._control_lock:
            if self._terminated:
                raise HostControlError("staging_host_terminated", "the staging host is stopping or has terminated")
            self._ensure_assembly(pinned)
            if self.session is None or self.session.application.state == "stopped":
                await self._load_session()
            if self.session.application.state != "new" or self._accepting:
                raise HostControlError("staging_screen_session_active", "stop intake before changing Screen authority")
            registry = self.native_extensions
            declarations = tuple(declaration for declaration in registry.declarations()
                if declaration.descriptor.tool.action == "screen.capture")
            if len(declarations) != 1:
                raise HostControlError("staging_screen_provider_unavailable", "one active native Screen provider is required")
            declaration = declarations[0]
            descriptor = declaration.descriptor
            if (registry.current_declaration(declaration.host_tool_id) != declaration
                    or declaration.host_tool_id != f"native:{SCREEN_PACKAGE_ID}:screen.capture"
                    or descriptor.package.extension_id != SCREEN_EXTENSION
                    or descriptor.binding.capability != SCREEN_PROCESS
                    or descriptor.binding.provider != "screen.native"
                    or descriptor.source_owner != "screen"):
                raise HostControlError("staging_screen_provider_changed", "loaded native Screen declaration changed")
            return settings, descriptor

    async def upgrade_screen(self, *, username: str, previous_version: str, previous_hash: str,
                             expected_revision: int) -> Outcome[dict[str, Any]]:
        async with self._lifecycle_lock:
            try:
                settings, descriptor = await self._screen_declaration()
                assert self.bootstrap is not None
                assert self.runtime is not None
                with self.runtime.transaction():
                    observed_at = self.now()
                    operator = dashboard_operator(self.runtime, username, at=observed_at)
                    management = CapabilityManagementService(self.runtime)
                    require(ObjectAccessPolicy(self.runtime).scope_access(operator,
                        scope=management.management_scope(SCREEN_EXTENSION), action="capability.update",
                        observed_at=observed_at))
                    upgraded = self.bootstrap.upgrade_screen(descriptor.package,
                        previous_version=previous_version, previous_hash=previous_hash,
                        expected_revision=expected_revision)
                    receipt = require(upgraded)
                result = Outcome.success({"upgraded": True, "profile_id": settings.profile_id,
                    "previous_version": receipt.previous_version,
                    "package_version": receipt.package_version, "artifact_hash": receipt.artifact_hash,
                    "revision": receipt.revision, "scope": receipt.scope, "started": False})
            except Exception as error:
                result = self._failure(error, "staging_screen_upgrade_failed")
            self._record_failure(result)
            return result

    async def revoke_delivery(self) -> Outcome[dict[str, Any]]:
        """Compatibility response; per-person permissions remain the delivery gate."""
        return Outcome.success({"authorized": True, "required": False, "expires_at": None,
                               "state": "not_required", "replayed": True,
                               "accepting": self._accepting})

    async def stop(self, *, grace_seconds: float = 0.5) -> Outcome[dict[str, Any]]:
        self._accepting = False
        async with self._lifecycle_lock:
            try:
                async with self._control_lock:
                    self._accepting = False
                    session = self.session
                if session is None:
                    result = Outcome.success({"state": "stopped", "pending_handles": [], "storage_closed": False})
                else:
                    report = await session.close(grace_seconds=grace_seconds)
                    async with self._control_lock:
                        if report.state == "stopped" and self.session is session:
                            self.last_session = session
                            self.session = None
                    result = Outcome.success(report)
            except Exception as error:
                result = self._failure(error, "staging_stop_failed")
            self._record_failure(result)
            return result

    def claims(self, event: object) -> bool:
        if is_notice_event(event):
            return False
        configuration = self.configuration
        session = self.session
        if not self._accepting or configuration is None or session is None:
            return False
        scope = configuration.scope
        if (
            not isinstance(event, self.native_types.event)
            or getattr(event, "unified_msg_origin", None)
            != f"{scope.platform}:FriendMessage:{scope.native_scope_id}"
            or session.application.state != "running"
        ):
            return False
        try:
            return (
                supported_platform_name(getattr(event.platform_meta, "name", None))
                and event_account_id(event) == scope.account_id
                and event_peer_id(event) == scope.native_scope_id
                and event.platform_meta.id == scope.platform
                and event.bot is session.bridge.client(scope)
            )
        except Exception:
            return False

    # ---- first-run binding ------------------------------------------------

    def binding_state(self) -> dict[str, Any]:
        """What is bound, and what the operator should do next.

        Reported from the pinned profile first and the parked binding second, matching
        ``resolved_settings``. ``pending`` is the difference that matters to the guide:
        a target has been named but nothing is pinned, so the next step is to confirm
        the identity rather than to name a conversation.
        """
        pinned = self._read_intent() if not self._multi_host else None
        if pinned is not None:
            return {
                "bound": True,
                "pinned": True,
                "platform_id": pinned.scope.platform,
                "account_id": pinned.scope.account_id,
                "participant_id": pinned.scope.native_scope_id,
                "bot_id": pinned.scope.bot_id,
                "role_id": pinned.persona.role.subject_id,
                "persona_id": pinned.persona.persona_id or "",
            }
        parked = self._parked_binding()
        if parked is None:
            return {"bound": False, "pinned": False, "pending": False}
        try:
            identity = parked.identity()
        except HostControlError as error:
            return {"bound": False, "pinned": False, "pending": False, "error": error.code}
        return {"bound": bool(parked.confirmed), "pinned": False,
                "pending": not parked.confirmed, "confirmed": parked.confirmed,
                **{name: identity[name] for name in
                   ("platform_id", "account_id", "participant_id", "bot_id", "role_id", "persona_id")}}

    def confirm_binding(self) -> dict[str, Any]:
        pinned = self._read_intent()
        if pinned is not None:
            return {"confirmed": True, "binding": self.binding_state()}
        parked = self._parked_binding()
        if parked is None:
            return {"confirmed": False, "reason": "setup_binding_missing",
                    "detail": "send 我会永远陪着你 from the core administrator private window first"}
        try:
            parked.identity()
        except HostControlError as error:
            return {"confirmed": False, "reason": error.code,
                    "detail": "the pending core administrator window has invalid identity"}
        if not parked.confirmed:
            write_binding(self.binding_root, PendingBinding(parked.platform_id, parked.account_id,
                parked.participant_id, parked.persona_id, confirmed=True))
        return {"confirmed": True, "binding": self.binding_state()}

    def handles_binding(self, event: object) -> bool:
        """Whether this private message is part of the bind vocabulary.

        Checked before ``claims``, which is exactly why it is narrow: it must answer
        for messages that will never be claimed, so a false positive here would route
        ordinary conversation into the setup path.
        """
        if is_notice_event(event):
            return False
        return is_private_message(event) and parse_bind_command(message_text(event)).kind != "ignore"

    async def receive_binding(self, event: object) -> dict[str, Any]:
        """Handle one bind/confirm/unbind command.

        **Nothing is sent back, on purpose.** A reply here would be the role
        speaking to a real person before the review step that exists to prevent
        exactly that -- and the delivery boundary makes it impossible anyway:
        binding happens before any session exists. So the outcome is reported through
        ``binding_state()`` for the guide to render, and the first message the
        operator ever sees this role compose stays the reviewed draft in step four.

        ``reply`` is therefore *advice for the page*, never text to transmit.

        The command is also not acknowledged in the chat even on refusal -- an
        unbound install answers nobody, which keeps a half-configured bot from
        looking live.
        """
        request = parse_bind_command(message_text(event))
        if request.kind == "ignore":
            return {"handled": False}
        identity = event_identity(event)
        if identity is None:
            return {"handled": True, "ok": False, "reason": "host_identity_unreadable",
                    "reply": "这条消息读不到平台、Bot 账号或发送者，无法作为绑定。"}

        if request.kind == "unbind":
            existing = self._read_intent()
            if existing is not None:
                return {"handled": True, "ok": False, "reason": "host_binding_exists",
                        "reply": "这个安装已经固定过目标；要更换请先在宿主管理里停止并清除运行档。"}
            removed = clear_binding(self.binding_root)
            if not removed and self.profile_root is not None:
                removed = clear_binding(self.profile_root)
            return {"handled": True, "ok": True, "removed": removed,
                    "reply": "已清除待绑定目标。" if removed else "当前没有待绑定的目标。",
                    "binding": self.binding_state()}

        existing = self._read_intent()
        if existing is not None:
            error = refuse_already_bound({
                "participant_id": existing.scope.native_scope_id or "（未记录）"})
            return {"handled": True, "ok": False, "reason": error.code, "reply": str(error)}

        if request.reason == "host_identity_invalid":
            return {"handled": True, "ok": False, "reason": request.reason,
                    "reply": "人格名不能包含换行或过长，请用一行短名字重发。"}

        persona_id = request.persona_id
        if request.kind == "bind" and not persona_id:
            persona_id = await self._window_persona(identity)
        parked = PendingBinding(platform_id=identity["platform_id"],
                                account_id=identity["account_id"],
                                participant_id=identity["participant_id"],
                                persona_id=persona_id)

        if request.kind == "confirm":
            existing_binding = self._parked_binding()
            if existing_binding is None:
                return {"handled": True, "ok": False, "reason": "host_binding_missing",
                        "reply": "还没有待确认的绑定，先发送「我会永远陪着你」。"}
            expected = (existing_binding.platform_id, existing_binding.account_id,
                        existing_binding.participant_id)
            actual = (identity["platform_id"], identity["account_id"], identity["participant_id"])
            if actual != expected:
                return {"handled": True, "ok": False, "reason": "host_binding_confirmation_mismatch",
                        "reply": "确认消息与待绑定的平台、Bot 账号或私聊对象不一致。请回到核心管理员窗口重新操作。"}
            return {"handled": True, "ok": True, "confirmed": existing_binding.confirmed,
                    "reply": "已核对这条私聊的来源。请回到「开始使用」，由 Dashboard 管理员完成绑定确认。",
                    "binding": self.binding_state()}

        existing_binding = self._parked_binding()
        if existing_binding is not None:
            expected = (existing_binding.platform_id, existing_binding.account_id,
                        existing_binding.participant_id)
            actual = (parked.platform_id, parked.account_id, parked.participant_id)
            if actual != expected:
                return {"handled": True, "ok": False, "reason": "host_binding_candidate_exists",
                        "reply": "已有待确认的管理员窗口。请在「开始使用」确认或清除后再绑定。"}
            parked = existing_binding
        try:
            write_binding(self.binding_root, parked)
            pinned = parked.identity()
        except (HostControlError, OSError, ValueError) as error:
            # write_binding 走 open/fsync/os.replace，磁盘与权限问题以 OSError 冒出；
            # 放行会让异常穿透已 stop_event 的绑定 handler，只在日志留一条 traceback。
            code = getattr(error, "code", "host_binding_write_failed")
            return {"handled": True, "ok": False, "reason": code,
                    "reply": "这条绑定无法成立：" + str(error)}
        return {"handled": True, "ok": True, "confirmed": False, "reply": describe_pin(pinned),
                "binding": self.binding_state()}

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

    async def receive_notice(self, event):
        if is_poke_notice(event):
            return await receive_poke(self, event)
        return await receive_notice(self, event)

    async def receive_claimed(self, event: object, *, on_refusal=None) -> Outcome[str]:
        session = self.session
        if not self.claims(event) or session is None:
            return Outcome.failed("staging_not_claimed", "the event is outside the active staging target")
        command_result = await receive_calendar_command(self, event, session)
        if command_result is not None:
            return command_result
        result = await session.receive(event)
        if result.is_success:
            self.latest_batch_id = result.unwrap()
            self.last_failure = None
        else:
            self._record_failure(result)
            if result.failure.code == "private_access_dialogue_denied":
                # 没有对话权限：按配置回一句（真实投递模式才发）。
                # 发送失败不影响"拒绝"本身，也不把插件标成不可受理。
                receipt = await self._refuse_private(session, event)
                if callable(on_refusal):
                    on_refusal(receipt)
            elif result.failure.code in (
                "authorization_denied",
                "authorization_expired",
                "authorization_not_active",
                "authorization_revoked",
                "scope_not_granted",
                "staging_delivery_grant_changed",
            ):
                self._accepting = False
        return result

    async def _refuse_private(self, session, event):
        """一句话的礼拒；任何失败都只当"没回话"，不改写拒绝结论。"""
        from .private_notice import notify_refusal

        try:
            return await notify_refusal(self, session, event,
                                        context=getattr(session, "last_refusal", None))
        except (TaskError, ValueError, TypeError, AttributeError, KeyError):
            return None
        finally:
            session.last_refusal = None

    async def inspect(self, batch_id: str | None = None) -> Outcome[dict[str, Any]]:
        async with self._control_lock:
            try:
                session = self.session or self.last_session
                if session is None:
                    raise HostControlError("staging_session_unavailable", "initialize the staging session first")
                result = session.inspect(batch_id)
                if result.failure is not None:
                    raise TaskError(result.failure.code, result.failure.reason)
                return Outcome.success(result.unwrap())
            except Exception as error:
                return self._failure(error, "staging_inspect_failed")

    async def preview(self, batch_id: str) -> Outcome[dict[str, Any]]:
        async with self._control_lock:
            try:
                if not batch_id or len(batch_id) > 512:
                    raise HostControlError("staging_batch_invalid", "supply one bounded batch_id")
                session = self.session or self.last_session
                if session is None:
                    raise HostControlError("staging_session_unavailable", "initialize the staging session first")
                result = session.preview(batch_id)
                if result.failure is not None:
                    raise TaskError(result.failure.code, result.failure.reason)
                return Outcome.success(result.unwrap())
            except Exception as error:
                return self._failure(error, "staging_preview_failed")

    def status(self) -> dict[str, Any]:
        settings = self.settings
        settings_error = self.settings_error.code if self.settings_error else None
        targets = []
        if settings is not None and settings_error is None:
            try:
                bound = self._with_bound_identity(settings)
                if bound.has_target:
                    targets = [{
                        "target_id": bound.profile_id,
                        "scope_kind": "private",
                        "platform_id": bound.platform_id,
                        "account_id": bound.account_id,
                        "bot_id": bound.bot_id,
                        "participant_id": bound.participant_id,
                        "roles": [{"role_id": bound.role_id,
                                   "persona_ids": [bound.persona_id] if bound.persona_id else []}],
                    }]
            except HostControlError as error:
                settings_error = error.code
        session = self.session or self.last_session
        intent = self.intent_path
        delivery_authorization = {"required": False, "authorized": True,
                                  "state": "not_required", "grant_id": None,
                                  "capability": None, "expires_at": None}
        return {
            "mode": "single",
            "enabled": bool(settings and settings.enabled),
            "settings_valid": settings_error is None,
            "settings_error": settings_error,
            "profile_id": settings.profile_id if settings else None,
            "targets": targets,
            "configured": bool(intent and intent.exists()),
            "runtime_open": self.runtime is not None,
            "session_state": session.application.state if session is not None else "unloaded",
            "review_only": session.review_only if session is not None else False,
            "accepting": self._accepting,
            "latest_batch_id": self.latest_batch_id,
            "last_failure": self.last_failure,
            "terminated": self._terminated,
            "model_request_sent_by_status": False,
            "platform_message_sent_by_status": False,
            "delivery_authorization": delivery_authorization,
            "parser_availability": parser_availability(),
        }

    def register_routes(self) -> None:
        self.web_api.register()

    def unregister_routes(self) -> None:
        self.web_api.unregister()

    async def terminate(self) -> None:
        self._accepting = False
        try:
            await self.session_topology.close()
            result = await self.stop(grace_seconds=2.0)
            stopped = False
            if result.is_success:
                report = result.unwrap()
                stopped = (
                    report.get("state") == "stopped"
                    if isinstance(report, dict)
                    else report.state == "stopped"
                )
            async with self._control_lock:
                if self.runtime is not None:
                    if not stopped:
                        # Release SQLite even after an incomplete drain so a later staged restore can replace it.
                        logger.warning("[CompanionV2] staging 停机未达 stopped，仍关闭存储 runtime 释放文件句柄")
                    self.runtime.close()
                    self.runtime = None
        finally:
            if self.native_extensions is not None:
                await self.native_extensions.aclose()
                self.native_extensions = None
            self._terminated = True
            self.unregister_routes()
