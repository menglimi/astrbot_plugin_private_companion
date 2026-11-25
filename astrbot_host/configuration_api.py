"""Dashboard-only editors for the configuration groups the cottage edits itself.

Groups that already had owners and consumers but no read-write route get one
here. Each section keeps its own edit token because the scopes differ: reaction
retrieval is installation-wide, the daily allowance is an installation/role
ceiling, and role behavior settings prefer role defaults with object pages
providing explicit window overrides. Personal permissions and private material
remain on their user, device or relationship routes.
"""

import asyncio
from collections import OrderedDict
from contextlib import nullcontext
from copy import deepcopy
import json
import logging
import threading
import time
from types import SimpleNamespace

from astrbot.api.web import request
from companion_v2.configuration.management import (AcquaintanceManagement, BusyGateManagement,
                                                   ContentAuthoringManagement,
                                                   DiaryAndRestManagement, GroupParticipationManagement,
                                                   ImageQuotaManagement,
                                                   ImageOutputManagement,
                                                   ImageReviewManagement, KnowledgeManagement,
                                                   LibraryManagement,
                                                   ModelBudgetManagement, ProactiveContactManagement,
                                                   PrivateAccessManagement, StrangerPrivateAccessManagement,
                                                   ReactionRetrievalManagement,
                                                   RecallManagement, ReferenceManagement,
                                                   WebUnderstandingManagement,
                                                   RoleGrowthSkillsManagement,
                                                   ExpressionLearningManagement,
                                                   RoleProfileManagement,
                                                   RelationshipProfileManagement,
                                                   SystemPromptManagement,
                                                   QzoneManagement, PhoneManagement,
                                                   RelayManagement,
                                                   ScreenManagement, WardrobeManagement)
from companion_v2.configuration.models import ConfigurationError
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.knowledge.configuration import KnowledgeSettings
from companion_v2.model_gateway.maintenance_cache import has_active_write_transaction
from companion_v2.relationships.account_links import KEY as ACCOUNT_LINKS_KEY
from companion_v2.storage.runtime import StorageOpenError

from .knowledge_sources import AstrBotKnowledgeSources

from .management_identity import dashboard_operator


PREFIX = "/astrbot_plugin_private_companion/config"
SECTION_CACHE_MAX_ENTRIES = 128
SECTION_CACHE_TTL_SECONDS = 30.0
logger = logging.getLogger(__name__)
SECTIONS = {"quota": ImageQuotaManagement, "image_review": ImageReviewManagement,
            "image.output": ImageOutputManagement,
            "busy_gate": BusyGateManagement,
            "content.authoring": ContentAuthoringManagement,
            "reaction": ReactionRetrievalManagement, "budget": ModelBudgetManagement,
            "recall": RecallManagement, "screen": ScreenManagement,
            "web_understanding": WebUnderstandingManagement,
            "relay": RelayManagement,
            # QQ 空间拓展的角色级开关与节流；安装与逐会话同意不在这里，归能力安装路由。
            "qzone": QzoneManagement,
            "device.phone": PhoneManagement,
            "person": AcquaintanceManagement,
            # Known users use role defaults; unbound accounts use installation settings.
            "private_access": PrivateAccessManagement,
            "private_access_installation": StrangerPrivateAccessManagement,
            "role.growth.skills": RoleGrowthSkillsManagement,
            "expression.learning": ExpressionLearningManagement,
            "knowledge": KnowledgeManagement, "life": DiaryAndRestManagement,
            "role_profile": RoleProfileManagement,
            "relationship_profile": RelationshipProfileManagement,
            "system.prompts": SystemPromptManagement,
            "library": LibraryManagement,
            "wardrobe": WardrobeManagement, "photo.reference": ReferenceManagement,
            "group_participation": GroupParticipationManagement,
            # 公共联系设置全局保存；个人偏好和授权在私聊用户页单独维护。
            "proactive.contact": ProactiveContactManagement}


def _document(raw, *, expected, optional=()):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
    if type(data) is not dict or not expected <= set(data) <= expected | set(optional):
        raise ValueError("unexpected or missing configuration fields")
    return data


class ConfigurationWebAPI:
    """One route family for the groups the cottage edits outside their own page."""

    def __init__(self, controller, *, allowed_sections=None):
        self.controller = controller
        self._section_cache = OrderedDict()
        self._section_cache_lock = threading.Lock()
        if allowed_sections is None:
            self.sections = SECTIONS
        else:
            names = tuple(allowed_sections)
            if not names or set(names) - set(SECTIONS) or len(set(names)) != len(names):
                raise ValueError("invalid configuration section allowlist")
            self.sections = {name: SECTIONS[name] for name in names}

    def _invalidate_section_cache(self):
        with self._section_cache_lock:
            self._section_cache.clear()

    def routes(self):
        routes = [(PREFIX, self.status, ["GET"], "Read allowed configuration sections"),
                (PREFIX + "/configure", self.configure, ["POST"],
                 "Save one allowed section with its own edit token")]
        if "group_participation" in self.sections:
            routes.extend([(PREFIX + "/behavior", self.behavior_status, ["GET"], "Read role-owned group habits"),
                           (PREFIX + "/behavior/command", self.behavior_command, ["POST"], "Review one learned group habit")])
        if "device.phone" in self.sections:
            routes.append((PREFIX + "/phone-connection", self.phone_connection, ["POST"], "Set up an Android phone for this role"))
            routes.append((PREFIX + "/phone-task", self.phone_task, ["POST"], "Start or inspect a private phone task"))
        return routes

    async def phone_task(self):
        from companion_v2.capabilities.privacy import is_core
        from .web_api import outcome_response

        try:
            raw = await request.body()
            if request.query or len(raw) > 4096:
                raise ValueError("invalid phone task request")
            data = _document(raw, expected={"action"}, optional=("goal", "shopping", "task_id"))
            controller = self.controller
            async with controller._control_lock:
                session = controller.session
                if controller._terminated or session is None or controller.runtime is None:
                    raise TaskError("phone_unavailable", "start the plugin before running a phone task")
                operator = dashboard_operator(controller.runtime, request.username, at=controller.now())
                if not is_core(controller.runtime, operator.subject):
                    raise TaskError("phone_admin_required", "phone tasks require an authenticated administrator")
                context = await self._management_context(controller, session, "", "")
                app = session.application
                app.require_running()
                if app.private_access_gate.denied(context, at=controller.now()):
                    raise TaskError("private_access_dialogue_denied", "the target private route is unavailable")
                service = app.phone_actions
                if data["action"] == "start" and set(data) <= {"action", "goal", "shopping"}:
                    if type(data.get("goal")) is not str or type(data.get("shopping", False)) is not bool:
                        raise ValueError("invalid phone goal")
                    result = service.create(context, data["goal"], shopping=data.get("shopping", False))
                elif data["action"] == "status" and set(data) == {"action", "task_id"}:
                    result = service.status(context, data["task_id"])
                else:
                    raise ValueError("unknown phone task request")
                return outcome_response(Outcome.success(result))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return outcome_response(Outcome.failed("phone_task_request_invalid", "invalid UTF-8 phone task request"))

    async def phone_connection(self):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            raw = await request.body()
            if request.query or len(raw) > 2048:
                raise ValueError("invalid phone connection request")
            data = _document(raw, expected={"action"},
                             optional=("address", "code", "serial", "edit_token", "operation_key",
                                       "include_diagnostics", "target_id", "role_id"))
            include_diagnostics = data.pop("include_diagnostics", False)
            if type(include_diagnostics) is not bool:
                raise ValueError("invalid diagnostic flag")
            target_id = data.pop("target_id", "")
            role_id = data.pop("role_id", "")
            if type(target_id) is not str or type(role_id) is not str:
                raise ValueError("invalid target or role selection")
            controller = self.controller
            if controller._terminated:
                raise TaskError("staging_host_terminated", "plugin is stopped")
            session, runtime = controller.session or controller.last_session, controller.runtime
            if session is None or runtime is None:
                raise TaskError("staging_not_initialized", "load the V2 profile first")
            context = await self._management_context(controller, session, target_id, role_id)

            def connect():
                operator = dashboard_operator(runtime, username, at=controller.now())
                return PhoneManagement(session.application, context).connection(
                    operator, data, include_diagnostics=include_diagnostics)

            result = await asyncio.to_thread(connect)
            if data.get("action") == "use":
                self._invalidate_section_cache()
            return outcome_response(result)
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error), details=getattr(error, "details", None)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("configuration_storage_unavailable", "the configuration store is not available"))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("device_phone_connection_invalid", "invalid UTF-8 phone connection request"))

    async def _run_behavior(self, *, command=False):
        from companion_v2.conversation.behavior.management import BehaviorManagement
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("group_behavior_command_invalid", "unexpected behavior query")
            data = None
            if command:
                raw = await request.body()
                if len(raw) > 4096:
                    raise ValueError("behavior command is too large")
                data = _document(raw, expected={"group_key", "habit_id", "enabled", "revision"})
            if not command:
                async with self.controller._control_lock:
                    controller = self.controller
                    if controller._terminated:
                        raise TaskError("staging_host_terminated", "plugin is stopped")
                    session = controller.session or controller.last_session
                    runtime = controller.runtime
                    if session is None or runtime is None:
                        raise TaskError("staging_not_initialized", "load the V2 profile first")
                    observed_at = controller.now()

                def load_behavior():
                    operator = dashboard_operator(runtime, username, at=observed_at)
                    manager = BehaviorManagement(session.application, session.profile.target)
                    return manager.status(operator)

                return outcome_response(Outcome.success(await asyncio.to_thread(load_behavior)))
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                session = controller.session or controller.last_session
                runtime = controller.runtime
                if session is None or runtime is None:
                    raise TaskError("staging_not_initialized", "load the V2 profile first")
                observed_at = controller.now()

                def run_behavior_command():
                    operator = dashboard_operator(runtime, username, at=observed_at)
                    manager = BehaviorManagement(session.application, session.profile.target)
                    return manager.command(operator, data)

                payload = await asyncio.to_thread(run_behavior_command)
                return outcome_response(Outcome.success(payload))
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("configuration_storage_unavailable", "the configuration store is not available"))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return outcome_response(Outcome.failed("group_behavior_command_invalid", "invalid behavior request"))

    async def behavior_status(self):
        return await self._run_behavior()

    async def behavior_command(self):
        return await self._run_behavior(command=True)

    async def _management_context(self, controller, session, target_id, role_id):
        """Resolve the page's selected target instead of always the private profile."""
        active = session.profile.target
        # TargetControllerView already resolves and validates the role route.
        # TaskContext has no target_id; compare the page selection with the
        # scoped controller instead of reinterpreting it as a different window.
        route_target = getattr(controller, "target_id", None)
        if route_target:
            settings = getattr(controller, "settings", None)
            canonical_role = (
                settings.canonical_role_id(role_id)
                if role_id and settings is not None and hasattr(settings, "canonical_role_id")
                else role_id
            )
            if ((target_id and target_id != route_target)
                    or (canonical_role and canonical_role != controller.role_id)):
                raise TaskError("configuration_target_changed",
                                "the selected target or role differs from the configuration route")
            return active
        if not target_id or target_id == getattr(active, "target_id", None):
            return active
        # Installation-only editors run under a synthetic SYSTEM scope. They have
        # no per-window target to switch to; a page selection must not turn their
        # save into a group/private window write.
        if getattr(getattr(active, "scope", None), "kind", None) is ScopeKind.SYSTEM:
            return active
        from .group_settings import GroupTarget
        try:
            target = controller.settings.target(target_id)
        except (KeyError, AttributeError):
            raise TaskError("configuration_target_unavailable",
                            "the selected target is unavailable") from None
        if target is None:
            raise TaskError("configuration_target_unavailable", "the selected target is unavailable")
        if not isinstance(target, GroupTarget):
            raise TaskError("configuration_target_inactive",
                            "only the active private conversation can edit its settings")
        bridge = session.bridge.groups
        account = bridge.accounts.get((target.platform_id, target.account_id))
        if account is None:
            raise TaskError("configuration_target_unavailable", "the selected group account is unavailable")
        conversation_id, role, _instruction = await bridge._role(account, target.group_id)
        if role_id and role.subject_id != role_id:
            raise TaskError("configuration_target_changed", "the selected group now uses another role")
        scope = ScopeRef(controller.runtime.installation_id, target.platform_id, ScopeKind.GROUP,
                         target.group_id, target.bot_id, target.account_id, conversation_id, target.group_id)
        if bridge.target_for_scope(scope) != target:
            raise TaskError("configuration_target_changed", "the selected group route changed")
        resolved = session.application.tasks.identity.resolve_role(scope, observed_at=controller.now())
        binding = resolved.unwrap() if resolved.is_success else SimpleNamespace(role=role, revision=0)
        if binding.role != role:
            raise TaskError("configuration_target_changed", "the group role binding changed")
        return SimpleNamespace(scope=scope, role_binding=binding,
            key=digest(["group-config-context.v1", scope.document(), binding.revision]),
            management_scope="group")

    async def _run(self, action):
        from .web_api import outcome_response
        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            query = request.query
            if (set(query.keys()) - {"sections", "target_id", "role_id"}
                    or any(len(query.getlist(key)) != 1 for key in query.keys())):
                raise TaskError("configuration_request_invalid", "unexpected configuration query")
            data = None
            if action == "configure":
                raw = await request.body()
                if len(raw) > 32768:
                    raise ValueError("configuration request is too large")
                data = _document(raw, expected={"section", "operation_key", "edit_token", "changes"},
                                 optional=("reset", "target_id", "role_id"))
                if data["section"] not in self.sections:
                    raise TaskError("configuration_request_invalid", "unknown configuration section")
                reset = data.get("reset", [])
                if (type(data["operation_key"]) is not str or not data["operation_key"].strip()
                        or len(data["operation_key"]) > 128 or type(data["edit_token"]) is not str
                        or len(data["edit_token"]) != 64 or type(data["changes"]) is not dict
                        or (not data["changes"] and not reset)
                        or type(reset) is not list or any(type(name) is not str for name in reset)
                        or len(set(reset)) != len(reset)):
                    raise ValueError("provide a stable operation key, edit token and explicit changes")
            # 这里刻意不拿 controller._control_lock：同一个 Dashboard 里目录、
            # 状态、模型等请求都可能在这把锁里跑上几秒，配置保存排在它们后面
            # 就会撞上前端的 15 秒上限（表现出来就是「保存中…」很久 + 未确认）。
            # 写入本身由 edit_token 与 tasks.check_generation() 两道栅栏保护，
            # 不依赖控制锁串行；这里只取一次控制器当前快照。
            controller = self.controller
            if controller._terminated:
                raise TaskError("staging_host_terminated", "plugin is stopped")
            session = controller.session or controller.last_session
            if session is None or controller.runtime is None:
                raise TaskError("staging_not_initialized", "load the V2 profile first")
            runtime = controller.runtime
            application = session.application
            context = await self._management_context(
                controller, session,
                data.get("target_id", "") if isinstance(data, dict) else query.get("target_id", ""),
                data.get("role_id", "") if isinstance(data, dict) else query.get("role_id", ""))
            observed_at = controller.now()
            if action == "status":
                requested = self._requested(query)

                def read_all():
                    operator = dashboard_operator(runtime, username, at=observed_at)
                    return self._read_sections_sync(application, context, operator, requested)

                sections, section_errors = await asyncio.to_thread(read_all)
                await self._enrich_knowledge_section(sections, host_context=controller.context)
                result = Outcome.success({"sections": sections, "section_errors": section_errors})
            else:
                started = time.perf_counter()
                def write():
                    operator = dashboard_operator(runtime, username, at=observed_at)
                    editor = SECTIONS[data["section"]](application, context)
                    body = {key: data[key] for key in ("operation_key", "edit_token", "changes")}
                    if reset:
                        body["reset_to_inherit"] = reset
                    saved = editor.configure(operator, body)
                    if saved.is_success:
                        # The operation receipt confirms the write. Pages refresh status
                        # separately so a slow projection cannot turn a committed write
                        # into an unconfirmed POST.
                        return Outcome.success({"result": saved.unwrap()})
                    return saved

                # V2 存储是同步 SQLite（单连接 + 全局锁）。放到工作线程里执行，
                # 事务再慢也只占用那个线程，不会卡住 AstrBot 的事件循环。
                result = await asyncio.to_thread(write)
                self._invalidate_section_cache()
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                if elapsed_ms >= 500:
                    logger.warning("[CompanionV2] 配置保存耗时 section=%s ms=%s",
                                   data["section"], elapsed_ms)
            return outcome_response(result)
        except (TaskError, ConfigurationError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            # 写入改到工作线程后，插件可能在两次取用之间完成重载并关掉运行时。
            # 这时给一个可重试的明确结果，而不是让异常冒成 500。
            return outcome_response(Outcome.failed(
                "configuration_storage_unavailable", "the configuration store is not available"))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed("configuration_request_invalid",
                                                   "invalid UTF-8 configuration request"))

    def _requested(self, query):
        raw = query.get("sections", "") or ""
        wanted = tuple(self.sections) if not raw else tuple(part.strip() for part in raw.split(",") if part.strip())
        if not wanted or set(wanted) - set(self.sections) or len(set(wanted)) != len(wanted):
            raise TaskError("configuration_request_invalid", "unknown configuration section")
        return wanted

    @staticmethod
    def _config_cache_token(app, runtime, config_store):
        """Return an O(1) revision token covering configuration, identity, policy, and account links.

        Unlike a whole-database write counter, unrelated background ticks or
        conversation turns in the main store do not falsely invalidate cached
        configuration sections.
        """
        if runtime is None or has_active_write_transaction(runtime):
            return None
        store = config_store if config_store is not None else runtime
        read_main = getattr(runtime, "read_one", None)
        read_cfg = getattr(store, "read_one", None)
        if not callable(read_main) or not callable(read_cfg):
            return None
        try:
            main_row = read_main(
                """SELECT
                       (SELECT COALESCE(revision, 0) FROM identity_policy_metadata WHERE singleton = 1),
                       (SELECT COALESCE(revision, 0) FROM object_policy_metadata WHERE singleton = 1),
                       (SELECT COALESCE(MAX(sequence), 0) FROM persona_binding_events),
                       (SELECT COUNT(*) || ':' || COALESCE(SUM(revision), 0) FROM capability_settings)""",
                (),
            )
            cfg_row = read_cfg(
                """SELECT
                       (SELECT COUNT(*) || ':' || COALESCE(SUM(revision), 0) FROM configuration_targets),
                       (SELECT COALESCE(MAX(sequence), 0) FROM configuration_changes),
                       (SELECT COUNT(*) || ':' || COALESCE(SUM(revision), 0) FROM configuration_values),
                       (SELECT COALESCE(value, '') FROM runtime_metadata WHERE key = ?)""",
                (ACCOUNT_LINKS_KEY,),
            )
        except Exception:
            return None
        registry = getattr(getattr(app, "configuration", None), "registry", None)
        schema_fp = getattr(registry, "fingerprint", None)
        return (
            tuple(main_row) if main_row is not None else None,
            tuple(cfg_row) if cfg_row is not None else None,
            schema_fp,
        )

    def _read_sections_sync(self, app, context, operator, requested):
        sections, section_errors = {}, {}
        runtime = getattr(app, "runtime", None)
        config_store = getattr(getattr(app, "configuration", None), "store", None)
        db_token = self._config_cache_token(app, runtime, config_store)
        now = app.now() if callable(getattr(app, "now", None)) else None
        context_key = getattr(context, "key", None)
        op_key = (
            getattr(operator, "subject", None),
            getattr(operator, "binding_id", None),
            getattr(operator, "binding_revision", None),
        )
        missed = []
        if db_token is not None and now is not None:
            with self._section_cache_lock:
                for name in requested:
                    cache_key = (id(app), context_key, op_key, name, self.sections[name])
                    cached = self._section_cache.get(cache_key)
                    if cached is not None:
                        cached_token, cached_at, cached_payload = cached
                        if (cached_token == db_token and cached_at <= now
                                and (now - cached_at).total_seconds() <= SECTION_CACHE_TTL_SECONDS):
                            self._section_cache.move_to_end(cache_key)
                            sections[name] = deepcopy(cached_payload)
                            continue
                        self._section_cache.pop(cache_key, None)
                    missed.append(name)
        else:
            missed = list(requested)

        loaded_for_cache = []
        if missed:
            main_ctx = runtime.read_snapshot() if callable(getattr(runtime, "read_snapshot", None)) else nullcontext()
            cfg_ctx = (
                config_store.read_snapshot()
                if (config_store is not None and config_store is not runtime
                    and callable(getattr(config_store, "read_snapshot", None)))
                else nullcontext()
            )
            with main_ctx, cfg_ctx:
                for name in missed:
                    try:
                        started = time.perf_counter()
                        result = self.sections[name](app, context).status(operator)
                        elapsed_ms = round((time.perf_counter() - started) * 1000)
                        if elapsed_ms >= 500:
                            logger.warning("[CompanionV2] 配置读取耗时 section=%s ms=%s", name, elapsed_ms)
                        if result.failure is not None:
                            if len(requested) == 1:
                                raise TaskError(result.failure.code, result.failure.reason)
                            section_errors[name] = {"code": result.failure.code}
                            continue
                        payload = {**result.unwrap(), "section": name}
                        sections[name] = payload
                        if db_token is not None:
                            loaded_for_cache.append((
                                (id(app), context_key, op_key, name, self.sections[name]),
                                deepcopy(payload),
                            ))
                    except (TaskError, ConfigurationError) as error:
                        sections.pop(name, None)
                        if len(requested) == 1:
                            raise
                        section_errors[name] = {"code": error.code}
                    except Exception:
                        sections.pop(name, None)
                        logger.exception("[CompanionV2] 配置分区读取失败 section=%s", name)
                        if len(requested) == 1:
                            raise TaskError("configuration_section_read_failed",
                                            "the configuration section could not be read") from None
                        section_errors[name] = {"code": "configuration_section_read_failed"}

        if (loaded_for_cache and db_token is not None and now is not None
                and self._config_cache_token(app, runtime, config_store) == db_token):
            with self._section_cache_lock:
                for cache_key, payload_copy in loaded_for_cache:
                    self._section_cache[cache_key] = (db_token, now, payload_copy)
                    self._section_cache.move_to_end(cache_key)
                while len(self._section_cache) > SECTION_CACHE_MAX_ENTRIES:
                    self._section_cache.popitem(last=False)

        ordered = {name: sections[name] for name in requested if name in sections}
        return ordered, section_errors

    @staticmethod
    async def _enrich_knowledge_section(sections, *, host_context=None):
        section = sections.get("knowledge")
        if section is None:
            return
        try:
            selected = KnowledgeSettings.from_values(section["settings"]).knowledge_source_ids()
        except Exception:
            selected = ()
        try:
            sources = await AstrBotKnowledgeSources(host_context).list_sources()
            sources_available = bool(getattr(host_context, "kb_manager", None))
        except Exception:
            sources = []
            sources_available = False
        available = {item["id"] for item in sources}
        available.update(doc["id"] for item in sources for doc in item.get("documents", ()))
        for identifier in selected:
            if identifier not in available:
                sources.append({"id": identifier, "type": "missing", "kb_id": "",
                                "name": "不可用来源 · " + identifier,
                                "documents": []})
        section["knowledge_sources"] = sources
        section["knowledge_sources_available"] = sources_available

    async def _status(self, app, context, operator, query, *, host_context=None, section_names=None):
        requested = tuple(section_names) if section_names is not None else self._requested(query)
        # 多分区读取放进同一工作线程与同一 MVCC 读快照，并结合写入令牌做轻量读缓存；
        # knowledge 的扩展来源仍是异步接口，留在事件循环上取。
        sections, section_errors = await asyncio.to_thread(
            self._read_sections_sync, app, context, operator, requested
        )
        await self._enrich_knowledge_section(sections, host_context=host_context)
        return {"sections": sections, "section_errors": section_errors}

    async def status(self):
        return await self._run("status")

    async def configure(self):
        return await self._run("configure")
