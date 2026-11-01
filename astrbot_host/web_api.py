"""Dashboard routes and response serialization for one staging controller."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import inspect
import json
import logging
from typing import Any, TYPE_CHECKING

from astrbot.api.web import request

from companion_v2.foundation.outcome import Outcome
from .settings import HostControlError
from .relationships_api import RelationshipWebAPI
from .backup_api import BackupWebAPI
from .memory_api import MemoryWebAPI
from .life_api import LifeWebAPI
from .growth_api import GrowthWebAPI
from .state_api import StateWebAPI
from .reply_api import ReplyWebAPI
from .model_api import ModelWebAPI
from .page_api import PageWebAPI
from .acquaintance_api import AcquaintanceWebAPI
from .integration_api import IntegrationWebAPI
from .semantic_api import SemanticWebAPI
from .perception_api import PerceptionWebAPI
from .native_perception_api import NativePerceptionWebAPI
from .setup_api import SetupWebAPI
from .legacy_api import LegacyMigrationWebAPI
from .configuration_api import ConfigurationWebAPI
from .persona_workshop_api import PersonaWorkshopWebAPI
from .reality_access_api import RealityAccessWebAPI
from .screen_access_api import ScreenAccessWebAPI
from .game_access_api import GameAccessWebAPI
from .native_extension_api import NativeExtensionWebAPI
from .account_session_api import AccountSessionWebAPI
from .image_material_api import ImageMaterialsWebAPI
from .qzone_api import QzoneWebAPI
from .host_options_api import HostOptionsWebAPI
from .storage_api import StorageWebAPI
from .display_names import display_names
from .huawei_health.api import HuaweiHealthWebAPI

if TYPE_CHECKING:
    from .controller import AstrBotStagingController


API_PREFIX = "/astrbot_plugin_private_companion/staging"


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def outcome_response(result: Outcome, *, message: str = "") -> dict[str, Any]:
    if result.failure is not None:
        # The dashboard page bridge forwards only ``message`` to plugin pages and
        # drops the rest of the envelope, so the machine code must travel there
        # for the pages to translate it; the human-readable reason stays beside it.
        return {
            "status": "error",
            "message": result.failure.code,
            "reason": result.failure.reason,
            "data": {"code": result.failure.code},
        }
    return {"status": "ok", "message": message, "data": _json_value(result.unwrap())}


logger = logging.getLogger(__name__)


def http_handler(handler):
    """Wrap a route handler so the dashboard always receives its JSON envelope.

    Unhandled exceptions used to escape into the ASGI server's 500 path. They
    now become the same ``{status, message, data}`` error every other route
    returns, which keeps one malformed request from producing a framework-level
    error response. Serialization itself stays with the host: the plugin never
    writes ``Content-Length`` for these JSON bodies.
    """

    async def respond(*args, **kwargs):
        try:
            value = handler(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
        except Exception as error:  # noqa: BLE001 - the dashboard must get JSON, not a broken pipe
            logger.warning("[CompanionV2] 面板接口未处理异常 handler=%s error_type=%s",
                           getattr(handler, "__name__", "unknown"), type(error).__name__)
            return {"status": "error", "message": "plugin_web_internal_error",
                    "reason": "the panel request failed", "data": {"code": "plugin_web_internal_error"}}
        return value

    return respond


class StagingWebAPI:
    """Own only this controller's route handlers, including partial registration cleanup."""

    def __init__(self, controller: AstrBotStagingController):
        self.controller = controller
        self._registered_routes: list[tuple[str, object]] = []
        self.relationships = RelationshipWebAPI(controller)
        self.backup = BackupWebAPI(controller)
        self.memory = MemoryWebAPI(controller)
        self.life = LifeWebAPI(controller)
        self.growth = GrowthWebAPI(controller)
        self.state = StateWebAPI(controller)
        self.reply = ReplyWebAPI(controller)
        self.model = ModelWebAPI(controller)
        self.integration = IntegrationWebAPI(controller)

    def register(self) -> None:
        register = getattr(self.controller.context, "register_web_api", None)
        if not callable(register):
            raise HostControlError("staging_web_api_unavailable", "this AstrBot host does not support plugin Web APIs")
        routes = [
            ("/status", self.api_status, ["GET"], "Companion V2 staging status"),
            ("/probe", self.api_probe, ["POST"], "Probe Companion V2 native target"),
            ("/initialize", self.api_initialize, ["POST"], "Initialize Companion V2 staging profile"),
            ("/authorize-delivery", self.api_authorize_delivery, ["POST"], "Authorize finite Companion V2 delivery"),
            ("/prepare-screen", self.api_prepare_screen, ["POST"], "Prepare native Screen for the pinned private target"),
            ("/upgrade-screen", self.api_upgrade_screen, ["POST"], "Upgrade the reviewed native Screen package"),
            ("/revoke-delivery", self.api_revoke_delivery, ["POST"], "Revoke Companion V2 delivery"),
            ("/start", self.api_start, ["POST"], "Start Companion V2 staging intake"),
            ("/stop", self.api_stop, ["POST"], "Stop Companion V2 staging intake"),
            ("/inspect", self.api_inspect, ["GET"], "Inspect Companion V2 staging evidence"),
            ("/preview", self.api_preview, ["GET"], "Read Companion V2 preview draft"),
        ]
        registered = getattr(self.controller.context, "registered_web_apis", None)
        snapshot = list(registered) if isinstance(registered, list) else None
        try:
            all_routes = [(API_PREFIX + suffix, handler, methods, description)
                          for suffix, handler, methods, description in routes] + self.relationships.routes() + self.backup.routes() + self.memory.routes() + self.life.routes() + self.growth.routes() + self.state.routes() + self.reply.routes() + self.model.routes() + PageWebAPI(self.controller).routes() + self.integration.routes() + AcquaintanceWebAPI(self.controller).routes()
            all_routes += SemanticWebAPI(self.controller).routes()
            all_routes += PerceptionWebAPI(self.controller).routes()
            all_routes += NativeExtensionWebAPI(self.controller).routes()
            all_routes += NativePerceptionWebAPI(self.controller).routes()
            all_routes += HuaweiHealthWebAPI(self.controller).routes()
            all_routes += ConfigurationWebAPI(self.controller).routes()
            all_routes += SetupWebAPI(self.controller).routes()
            all_routes += LegacyMigrationWebAPI(self.controller).routes()
            # Persona text stays host-owned; the workshop keeps editable text versions
            # and can produce an uncommitted structured role-profile draft.
            all_routes += PersonaWorkshopWebAPI(self.controller).routes()
            all_routes += RealityAccessWebAPI(self.controller).routes()
            all_routes += ScreenAccessWebAPI(self.controller).routes()
            all_routes += GameAccessWebAPI(self.controller).routes()
            all_routes += AccountSessionWebAPI(self.controller).routes()
            all_routes += ImageMaterialsWebAPI(self.controller).routes()
            all_routes += QzoneWebAPI(self.controller).routes()
            all_routes += HostOptionsWebAPI(self.controller).routes()
            all_routes += StorageWebAPI(self.controller).routes()
            for route, handler, methods, description in all_routes:
                wrapped = http_handler(handler)
                register(route, wrapped, methods, description)
                self._registered_routes.append((route, wrapped))
        except Exception:
            if snapshot is not None:
                registered[:] = snapshot
                self._registered_routes.clear()
            else:
                self.unregister()
            raise

    def unregister(self) -> None:
        registered = getattr(self.controller.context, "registered_web_apis", None)
        if not isinstance(registered, list):
            return
        owned = {(route, id(handler)) for route, handler in self._registered_routes}
        registered[:] = [
            item
            for item in registered
            if not (
                isinstance(item, tuple)
                and len(item) >= 2
                and (item[0], id(item[1])) in owned
            )
        ]
        self._registered_routes.clear()

    async def api_status(self) -> dict[str, Any]:
        status = self.controller.status()
        try:
            username = request.username
        except RuntimeError:
            username = None
        if type(username) is str and username.strip() and not username.startswith("api_key:"):
            status["targets"] = await display_names(self.controller).targets(status.get("targets", []))
            if self.controller._terminated:
                status = self.controller.status()
        return {"status": "ok", "data": status}

    async def api_probe(self) -> dict[str, Any]:
        return outcome_response(await self.controller.probe())

    async def api_initialize(self) -> dict[str, Any]:
        return outcome_response(await self.controller.initialize_profile())

    async def api_authorize_delivery(self) -> dict[str, Any]:
        return outcome_response(await self.controller.authorize_delivery())

    async def api_prepare_screen(self) -> dict[str, Any]:
        return outcome_response(await self.controller.prepare_screen())

    async def api_upgrade_screen(self) -> dict[str, Any]:
        try:
            if request.query:
                raise ValueError("query fields are not allowed")
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                return outcome_response(Outcome.failed("dashboard_identity_required", "sign in to the AstrBot Dashboard"))
            raw = await request.body()
            if len(raw) > 4096:
                raise ValueError("request is too large")
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("duplicate field")
                    values[key] = value
                return values
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
            if (type(data) is not dict or set(data) !=
                    {"previous_version", "previous_hash", "expected_revision"}):
                raise ValueError("provide the exact previous package and revision")
            return outcome_response(await self.controller.upgrade_screen(username=username, **data))
        except (ValueError, TypeError, UnicodeError, KeyError):
            return outcome_response(Outcome.failed("staging_screen_upgrade_invalid", "invalid Screen upgrade request"))

    async def api_revoke_delivery(self) -> dict[str, Any]:
        return outcome_response(await self.controller.revoke_delivery())

    async def api_start(self) -> dict[str, Any]:
        return outcome_response(await self.controller.start())

    async def api_stop(self) -> dict[str, Any]:
        return outcome_response(await self.controller.stop())

    @staticmethod
    def _batch_query() -> str | None:
        value = request.query.get("batch_id")
        return str(value).strip() if value is not None else None

    async def api_inspect(self) -> dict[str, Any]:
        return outcome_response(await self.controller.inspect(self._batch_query()))

    async def api_preview(self) -> dict[str, Any]:
        return outcome_response(await self.controller.preview(self._batch_query() or ""))
