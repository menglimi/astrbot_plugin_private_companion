"""Dashboard control and immutable per-owner administration routes."""

from types import SimpleNamespace
import logging

from astrbot.api.web import request
from companion_v2.execution.models import TaskError, digest
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from .memory_api import MemoryWebAPI, PREFIX as MEMORY_PREFIX
from .state_api import StateWebAPI, PREFIX as STATE_PREFIX
from .reply_api import ReplyWebAPI, PREFIX as REPLY_PREFIX
from .configuration_api import ConfigurationWebAPI, PREFIX as CONFIGURATION_PREFIX, SECTIONS
from .model_api import ModelWebAPI, PREFIX as MODEL_PREFIX
from .life_api import LifeWebAPI, PREFIX as LIFE_PREFIX
from .relationships_api import RelationshipWebAPI, MANAGEMENT_PREFIX as RELATIONSHIP_PREFIX
from .growth_api import GrowthWebAPI, PREFIX as GROWTH_PREFIX
from .web_api import StagingWebAPI, outcome_response
from .group_settings import GroupTarget
from .group_memory_api import GroupMemoryWebAPI
from .group_model_api import GroupModelWebAPI
from .group_reply_api import GroupReplyWebAPI
from .page_api import PageWebAPI
from .acquaintance_api import AcquaintanceWebAPI
from .integration_api import IntegrationWebAPI
from .semantic_api import SemanticWebAPI
from .setup_api import SetupWebAPI
from .account_session_api import AccountSessionWebAPI
from .perception_api import PerceptionWebAPI
from .native_extension_api import NativeExtensionWebAPI
from .native_perception_api import NativePerceptionWebAPI, PREFIX as NATIVE_PERCEPTION_PREFIX
from .persona_workshop_api import PersonaWorkshopWebAPI, PREFIX as PERSONA_PREFIX
from .legacy_api import LegacyMigrationWebAPI, PREFIX as LEGACY_PREFIX
from .legacy_group_data_api import LegacyGroupDataWebAPI
from .backup_api import BackupWebAPI
from .reality_access_api import RealityAccessWebAPI, PREFIX as REALITY_ACCESS_PREFIX
from .screen_access_api import ScreenAccessWebAPI, PREFIX as SCREEN_ACCESS_PREFIX
from .game_access_api import GameAccessWebAPI, PREFIX as GAME_ACCESS_PREFIX
from .image_material_api import ImageMaterialsWebAPI, PREFIX as IMAGE_MATERIAL_PREFIX
from .qzone_api import QzoneWebAPI, PREFIX as QZONE_PREFIX
from .host_options_api import HostOptionsWebAPI
from .storage_api import StorageWebAPI
from .display_names import display_names
from .identity_derivation import role_display_name
from .role_paths import HOST_PREFIX as PREFIX, role_legacy_paths
from .performance_mode import performance_enabled, performance_status
from .huawei_health.api import HuaweiHealthWebAPI


logger = logging.getLogger(__name__)

INSTALLATION_CONFIGURATION_SECTIONS = ("proactive.contact", "reaction", "budget")
TARGET_CONFIGURATION_SECTIONS = tuple(name for name in SECTIONS
    if name not in INSTALLATION_CONFIGURATION_SECTIONS)


def configuration_routes(reply_route, view, *, allowed_sections=None):
    """Rebuild the shared configuration routes on one role's own path.

    ``reply_route`` ends with ``/reply``; the configuration routes keep their own
    ``/config`` suffix, so the prefix is swapped rather than stripped.
    """
    base = reply_route[: -len("/reply")] if reply_route.endswith("/reply") else reply_route
    api = ConfigurationWebAPI(view, allowed_sections=allowed_sections)
    return tuple((path.replace(CONFIGURATION_PREFIX, base + "/config", 1), handler, methods, description)
                 for path, handler, methods, description in api.routes())


class TargetControllerView:
    """Do not store a globally selected target while concurrent requests await."""
    def __init__(self, controller, target_id, role_id):
        self.controller, self.target_id, self.role_id = controller, target_id, role_id

    def __getattr__(self, name):
        return getattr(self.controller, name)

    @property
    def session(self):
        controller = self.controller
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            return None
        try:
            context = controller.authority.context(self.target_id, self.role_id)
        except TaskError:
            # A role shared by several windows only has its context registered
            # on the primary route; page reads for an alias should use that same
            # role context instead of failing with a generic "unreadable" note.
            link = controller.settings.window_link_for_target(self.target_id)
            if link is None:
                raise
            context = controller.authority.context(link.primary_target_id, self.role_id)
        return SimpleNamespace(application=session.application, profile=SimpleNamespace(target=context))

    @property
    def last_session(self):
        return self.session


class InstallationControllerView:
    """Give installation-only editors a management scope, never a chat window."""

    def __init__(self, controller):
        self.controller = controller

    def __getattr__(self, name):
        return getattr(self.controller, name)

    @property
    def session(self):
        controller = self.controller
        session = controller.session or controller.last_session
        if session is None or controller.runtime is None:
            return None
        scope = ScopeRef(controller.runtime.installation_id, "companion", ScopeKind.SYSTEM,
                         "page.management")
        return SimpleNamespace(application=session.application,
                               profile=SimpleNamespace(target=SimpleNamespace(scope=scope)))

    @property
    def last_session(self):
        return self.session


class MultiHostWebAPI:
    unregister = StagingWebAPI.unregister

    def __init__(self, controller):
        self.controller = controller
        self._registered_routes = []

    def _register_routes(self, register, routes):
        registered = getattr(self.controller.context, "registered_web_apis", None)
        snapshot = list(registered) if isinstance(registered, list) else None
        try:
            for route, handler, methods, description in routes:
                register(route, handler, methods, description)
                self._registered_routes.append((route, handler))
        except Exception:
            if snapshot is not None:
                registered[:] = snapshot
                self._registered_routes.clear()
            else:
                self.unregister()
            raise

    def target_inventory(self):
        settings = self.controller.settings
        if settings is None:
            return []
        inventory = [{**vars(target), "scope_kind": "group" if isinstance(target, GroupTarget) else "private",
            "delivery_mode": settings.delivery_mode_for(target),
            "group_delivery_authorization": (self.controller.authority.group_delivery.status(target)
                if isinstance(target, GroupTarget) and self.controller.authority is not None
                and self.controller.runtime is not None else None), "roles": [
            {"role_id": role, "persona_ids": [p.persona_id for p in settings.personas if p.bot_id == target.bot_id and p.role_id == role],
             "role_name": role_display_name(role, [p.persona_id for p in settings.personas
                                                    if p.bot_id == target.bot_id and p.role_id == role]),
             "memory_api": (None if isinstance(target, GroupTarget) else
                            PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/memory"),
             "group_memory_api": (PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/group-memory"
                                  if isinstance(target, GroupTarget) else None),
             "state_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/state"),
             "reply_api": PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/reply",
             "config_api": (None if isinstance(target, GroupTarget) else
                            PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/config"),
             "role_config_api": None,
             "model_api": PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/model",
             "perception_native_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/perception-native"),
             "reality_access_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/reality-access"),
             "screen_access_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/screen-access"),
             "game_access_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/game-access"),
             "qzone_extension_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/qzone-extension"),
             "image_material_api": (None if isinstance(target, GroupTarget) else
                           PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/image-materials"),
             # 人格草稿按「角色 + 宿主人格」归属，所以群目标没有这一项：
             # 群没有独立人格文本，给一个空地址比给一个读不出东西的地址更诚实。
             "persona_api": (None if isinstance(target, GroupTarget) else
                             PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/persona"),
             "life_api": (None if isinstance(target, GroupTarget) else
                          PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/life"),
             "relationships_api": (None if isinstance(target, GroupTarget) else
                          PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/relationships"),
             "growth_api": (None if isinstance(target, GroupTarget) else
                          PREFIX + "/targets/" + target.target_id + "/roles/" + digest(role) + "/growth"),
             # V1 迁移写的是角色设置，所以它和别的域路由一样按角色挂：
             # 页面先选目标与角色，再对着那个角色的路径侦察、试算、执行。
             **role_legacy_paths(target.target_id, role,
                                 "group" if isinstance(target, GroupTarget) else "private")}
            for role in sorted({p.role_id for p in settings.personas if p.bot_id == target.bot_id})]}
            for target in settings.all_targets]
        self._ensure_role_routes(inventory)
        return inventory

    def _ensure_role_routes(self, inventory):
        """Refresh per-role routes when a role was mapped after this host started.

        ``register()`` builds one route per role known at boot. Saving a new
        persona mapping later used to leave the page manifest advertising a
        ``config_api`` (and every other per-role API) whose route did not exist;
        AstrBot then answered "未找到该路由" on the role-profile tab. Refresh once
        when the inventory proves a route is missing.
        """
        if getattr(self, "_role_routes_refresh_failed", False):
            return
        if getattr(self, "_refreshing_role_routes", False):
            return
        registered = {route for route, _ in self._registered_routes}
        expected = {role.get("config_api") for target in inventory
                    for role in target.get("roles", []) if role.get("config_api")}
        if not expected or expected <= registered:
            return
        self._refreshing_role_routes = True
        try:
            self.controller.unregister_routes()
            self.controller.register_routes()
        except Exception:
            self._role_routes_refresh_failed = True
            logger.warning("[CompanionV2] 角色路由刷新失败 error_type=%s",
                           "registration_failed")
        finally:
            self._refreshing_role_routes = False

    @staticmethod
    def _admin():
        from .management_identity import _authenticated_api_key
        username = request.username
        if type(username) is not str or not username.strip() or (username.startswith("api_key:") and not _authenticated_api_key(username)):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard as an administrator")

    async def api_status(self):
        try:
            self._admin()
            status = self.controller.status()
            status["targets"] = await display_names(self.controller).targets(status.get("targets", []))
            self._admin()
            if self.controller._terminated:
                status = self.controller.status()
            return {"status": "ok", "data": status}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def api_performance(self):
        try:
            self._admin()
            return {"status": "ok", "data": performance_status(
                getattr(self.controller, "config", {}),
                enabled=getattr(self.controller, "_performance_mode_enabled", None))}
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    def control(self, method, *, target=False):
        async def handle():
            try:
                self._admin()
                query = request.query
                if set(query.keys()) != ({"target_id"} if target else set()) or any(
                    len(query.getlist(k)) != 1 for k in query.keys()):
                    raise TaskError("host_request_invalid", "select exactly one configured target when required")
                arguments = {"target_id": query["target_id"]} if target else {}
                return outcome_response(await getattr(self.controller, method)(**arguments))
            except TaskError as error:
                return outcome_response(Outcome.failed(error.code, str(error)))
        return handle

    async def api_preview(self):
        try:
            self._admin()
            query = request.query
            if set(query.keys()) != {"target_id", "batch_id"} or any(
                len(query.getlist(k)) != 1 for k in query.keys()):
                raise TaskError("host_request_invalid", "select exactly one target and original input batch")
            return outcome_response(self.controller.preview(query["target_id"], query["batch_id"]))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def api_batches(self):
        try:
            self._admin()
            query = request.query
            if set(query.keys()) != {"target_id"} or len(query.getlist("target_id")) != 1:
                raise TaskError("host_request_invalid", "select exactly one configured target")
            return outcome_response(self.controller.batches(query["target_id"]))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    def register(self):
        register = getattr(self.controller.context, "register_web_api", None)
        if not callable(register):
            raise TaskError("host_web_api_unavailable", "this host does not support plugin Web APIs")
        routes = [
            (PREFIX + "/status", self.api_status, ["GET"], "Companion V2 multi-target status"),
            (PREFIX + "/performance", self.api_performance, ["GET"], "Companion V2 page performance mode"),
        ]
        lite_mode = performance_enabled(getattr(self.controller, "config", {}))
        self.controller._performance_mode_enabled = lite_mode
        if lite_mode:
            self._register_routes(register, routes)
            return
        routes += PageWebAPI(self.controller).routes() + IntegrationWebAPI(self.controller).routes() + AcquaintanceWebAPI(self.controller).routes()
        routes += SemanticWebAPI(self.controller).routes()
        routes += SetupWebAPI(self.controller).routes()
        routes += AccountSessionWebAPI(self.controller).routes()
        routes += PerceptionWebAPI(self.controller).routes()
        routes += NativeExtensionWebAPI(self.controller).routes()
        routes += HuaweiHealthWebAPI(self.controller).routes()
        # 备份是安装级的：它不需要目标，快照覆盖当前运行档的那棵 V2 数据根
        # （多目标下是 installations/<installation_key>/runtime）。所以这里挂一次，
        # 不像其它域路由那样按角色重挂。暂存还原的生效点在 multi_controller
        # 的加载路径上，通过同一个暂存还原入口处理。
        routes += BackupWebAPI(self.controller).routes()
        routes += HostOptionsWebAPI(self.controller).routes()
        routes += StorageWebAPI(self.controller).routes()
        # Installation-owned groups stay on one route, away from selected chats.
        routes += ConfigurationWebAPI(InstallationControllerView(self.controller),
            allowed_sections=INSTALLATION_CONFIGURATION_SECTIONS).routes()
        installation_perception = NativePerceptionWebAPI(InstallationControllerView(self.controller))
        routes.append((NATIVE_PERCEPTION_PREFIX + "/materials", installation_perception.material_pool,
                       ["GET"], "Read role-wide shared materials"))
        routes.append((PREFIX + "/preview", self.api_preview, ["GET"], "Companion V2 conversation preview"))
        routes.append((PREFIX + "/batches", self.api_batches, ["GET"], "Companion V2 recent target batches"))
        for action, method in (("probe", "probe"), ("initialize", "initialize_profile"), ("start", "start"),
                               ("stop", "stop"), ("authorize-delivery", "authorize_delivery"), ("revoke-delivery", "revoke_delivery"),
                               ("authorize-group-delivery", "authorize_group_delivery"),
                               ("revoke-group-delivery", "revoke_group_delivery")):
            routes.append((PREFIX + "/" + action, self.control(method, target="delivery" in action),
                           ["POST"], "Companion V2 " + action))
        for target in self.target_inventory():
            for role in target["roles"]:
                if role["group_memory_api"] is not None:
                    routes.extend(GroupMemoryWebAPI(self.controller, target["target_id"], role["role_id"]).routes(
                        role["group_memory_api"]))
                    routes.extend(GroupReplyWebAPI(self.controller, target["target_id"], role["role_id"]).routes(
                        role["reply_api"]))
                    routes.extend(GroupModelWebAPI(self.controller, target["target_id"], role["role_id"]).routes(
                        role["model_api"]))
                    routes.extend(LegacyGroupDataWebAPI(self.controller, target["target_id"], role["role_id"]).routes(
                        role["legacy_group_data_api"]))
                if role["memory_api"] is None:
                    continue
                source_target_id = getattr(self.controller, "role_source_target_id",
                    lambda _role_id, fallback: fallback)(role["role_id"], target["target_id"])
                view = TargetControllerView(self.controller, target["target_id"], role["role_id"])
                role_view = TargetControllerView(self.controller, source_target_id, role["role_id"])
                api = MemoryWebAPI(view)
                routes.extend((role["memory_api"] + path.removeprefix(MEMORY_PREFIX), handler, methods, description)
                              for path, handler, methods, description in api.routes())
                routes.extend((role["state_api"] + path.removeprefix(STATE_PREFIX), handler, methods, description)
                              for path, handler, methods, description in StateWebAPI(role_view).routes())
                routes.extend((role["reply_api"] + path.removeprefix(REPLY_PREFIX), handler, methods, description)
                              for path, handler, methods, description in ReplyWebAPI(view).routes())
                routes.extend((role["model_api"] + path.removeprefix(MODEL_PREFIX), handler, methods, description)
                              for path, handler, methods, description in ModelWebAPI(role_view).routes())
                routes.extend((role["perception_native_api"] + path.removeprefix(NATIVE_PERCEPTION_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in NativePerceptionWebAPI(view).routes())
                routes.extend((role["reality_access_api"] + path.removeprefix(REALITY_ACCESS_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in RealityAccessWebAPI(view).routes())
                routes.extend((role["screen_access_api"] + path.removeprefix(SCREEN_ACCESS_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in ScreenAccessWebAPI(view).routes())
                routes.extend((role["game_access_api"] + path.removeprefix(GAME_ACCESS_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in GameAccessWebAPI(view).routes())
                routes.extend((role["qzone_extension_api"] + path.removeprefix(QZONE_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in QzoneWebAPI(view).routes())
                routes.extend((role["life_api"] + path.removeprefix(LIFE_PREFIX), handler, methods, description)
                              for path, handler, methods, description in LifeWebAPI(role_view).routes())
                routes.extend((role["relationships_api"] + path.removeprefix(RELATIONSHIP_PREFIX), handler, methods, description)
                              for path, handler, methods, description in RelationshipWebAPI(view).routes())
                routes.extend((role["growth_api"] + path.removeprefix(GROWTH_PREFIX), handler, methods, description)
                              for path, handler, methods, description in GrowthWebAPI(view).routes())
                routes.extend((role["persona_api"] + path.removeprefix(PERSONA_PREFIX), handler, methods, description)
                              for path, handler, methods, description in PersonaWorkshopWebAPI(view).routes())
                if role.get("legacy_api") is not None:
                    routes.extend((role["legacy_api"] + path.removeprefix(LEGACY_PREFIX), handler, methods, description)
                                  for path, handler, methods, description in LegacyMigrationWebAPI(view).routes())
                routes.extend(configuration_routes(role["reply_api"], view,
                    allowed_sections=TARGET_CONFIGURATION_SECTIONS))
                routes.extend((role["image_material_api"] + path.removeprefix(IMAGE_MATERIAL_PREFIX),
                               handler, methods, description)
                              for path, handler, methods, description in ImageMaterialsWebAPI(view).routes())
        self._register_routes(register, routes)
