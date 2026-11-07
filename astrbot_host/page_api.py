"""Authenticated page directory; the host supplies every target and API path."""

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.web import request
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy

from .management_identity import dashboard_operator
from .display_names import display_names
from .group_remarks import GroupRemarks
from .identity_derivation import role_display_name
from .settings import HostControlError


PREFIX = "/astrbot_plugin_private_companion"
DISPLAY_PREFERENCES_FILE = "dashboard_display_preferences.json"
DISPLAY_PREFERENCES_KEYS = frozenset(("appearance", "followLight", "largeText"))
DISPLAY_APPEARANCE_VALUES = {
    "theme": frozenset(("courtyard", "dawn", "dusk", "night", "walnut", "contrast")),
    "density": frozenset(("compact", "standard", "roomy")),
    "radius": frozenset(("sharp", "standard", "round")),
    "typeScale": frozenset(("s", "m", "l", "xl")),
    "motion": frozenset(("full", "low", "off")),
}
MAX_DISPLAY_PREFERENCES_BYTES = 8192


def _valid_display_preferences(value):
    if type(value) is not dict or set(value) != DISPLAY_PREFERENCES_KEYS:
        return False
    appearance = value["appearance"]
    if type(appearance) is not dict or set(appearance) != set(DISPLAY_APPEARANCE_VALUES):
        return False
    for name, options in DISPLAY_APPEARANCE_VALUES.items():
        if type(appearance[name]) is not str or appearance[name] not in options:
            return False
    return type(value["followLight"]) is bool and type(value["largeText"]) is bool


class PageWebAPI:
    def __init__(self, controller):
        self.controller = controller
        self._display_preferences_lock = asyncio.Lock()

    def routes(self):
        return [(PREFIX + "/page/targets", self.targets, ["GET"], "Read authorized Companion page targets"),
                (PREFIX + "/page/workspace-entry", self.workspace_entry, ["GET"],
                 "Refresh the authenticated Companion workspace asset entry"),
                (PREFIX + "/page/ownership", self.ownership, ["GET"],
                 "Read which plugin currently provides model-facing memory"),
                (PREFIX + "/page/display-preferences", self.display_preferences, ["GET"],
                 "Read this Dashboard account's Companion display preferences"),
                (PREFIX + "/page/display-preferences", self.save_display_preferences, ["POST"],
                 "Save this Dashboard account's Companion display preferences")]

    async def workspace_entry(self):
        """Refresh the current page's short-lived resource token through AstrBot.

        Plugin pages run in an opaque-origin iframe, so their own fetch calls cannot
        read Dashboard endpoints. The registered plugin API bridge supplies the
        authenticated request context needed by AstrBot's page service.
        """

        from .web_api import outcome_response

        try:
            username = request.username
            if (
                type(username) is not str
                or not username.strip()
                or username.startswith("api_key:")
            ):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            plugin_name = PREFIX.lstrip("/")
            if request.plugin_name != plugin_name:
                raise TaskError("page_request_invalid", "the page entry is only available to this plugin")
            if request.query:
                raise TaskError("page_request_invalid", "the page entry does not accept query overrides")

            current_request = request._get_current()
            raw_request = getattr(current_request, "_request", None)
            app_state = getattr(getattr(raw_request, "app", None), "state", None)
            services = getattr(app_state, "services", None)
            page_service = getattr(services, "plugin_pages", None)
            get_entry = getattr(page_service, "get_plugin_page_entry_config", None)
            if not callable(get_entry):
                raise TaskError("page_entry_unavailable", "AstrBot page service is unavailable")

            locale = request.headers.get("accept-language", "").split(",", 1)[0].split(";", 1)[0].strip()
            if not locale or len(locale) > 32:
                locale = "zh-CN"
            entry = await get_entry(
                plugin_name=plugin_name,
                page_name="陪伴小屋",
                username=username,
                locale=locale,
            )
            content_path = entry.get("content_path") if isinstance(entry, dict) else None
            token = (
                parse_qs(urlsplit(content_path).query).get("asset_token", [""])[0]
                if isinstance(content_path, str)
                else ""
            )
            if not isinstance(content_path, str) or not content_path.strip() or not token:
                raise TaskError("page_entry_unavailable", "AstrBot returned an invalid page entry")
            return {"status": "ok", "message": "", "data": entry}
        except TaskError as error:
            if error.code == "page_entry_unavailable":
                logger.warning("[CompanionV2] 工作区授权刷新失败 code=%s", error.code)
            return outcome_response(Outcome.failed(error.code, str(error)))
        except Exception as error:
            logger.warning("[CompanionV2] 工作区授权刷新暂不可用 error_type=%s", type(error).__name__)
            return outcome_response(Outcome.failed(
                "page_entry_unavailable", "AstrBot could not refresh the workspace entry"))

    async def ownership(self):
        """这段时期记忆由谁对外提供，以及为什么不是 V2。

        这个回答是**安装级**的（判定读的是进程里别家插件公开的声明），所以它挂在
        页面目录旁边，而不是某一个会话目标下面——同一个安装下所有角色共享同一个
        回答，按目标重复一遍只会让页面以为它们可以不一样。

        页面据此写出「记忆由〈我会牢牢记住你〉提供」，所以判定失败时也必须给出
        原因码：宁可说「句柄读不到、暂时由 V2 自己提供」，也不能两边都不说话。
        """

        from .web_api import outcome_response

        try:
            controller = self.controller
            if controller._terminated:
                raise TaskError("staging_host_terminated", "plugin is stopped")
            session = controller.session or controller.last_session
            if session is None:
                raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
            return outcome_response(Outcome.success(session.application.memory.context.ownership()))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    def _display_preferences_identity(self):
        username = request.username
        if (type(username) is not str or not username.strip() or username.startswith("api_key:")
                or len(username.strip()) > 128):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if request.plugin_name != PREFIX.lstrip("/"):
            raise TaskError("page_request_invalid", "display preferences are only available to this plugin")
        if request.query:
            raise TaskError("page_request_invalid", "display preferences do not accept query overrides")
        if getattr(self.controller, "_terminated", False):
            raise TaskError("staging_host_terminated", "plugin is stopped")
        return username.strip()

    def _display_preferences_path(self):
        root = getattr(self.controller, "data_root", None)
        if root is None:
            raise TaskError("display_preferences_unavailable", "the plugin data directory is unavailable")
        return Path(root) / DISPLAY_PREFERENCES_FILE

    def _read_display_preferences(self):
        path = self._display_preferences_path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise TaskError("display_preferences_unavailable",
                            "the display preferences could not be read") from error
        if raw.startswith(b"\xef\xbb\xbf") or len(raw) > MAX_DISPLAY_PREFERENCES_BYTES * 16:
            return {}
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            return {}
        if (type(document) is not dict or document.get("version") != 1
                or type(document.get("users")) is not dict):
            return {}
        return {key: value for key, value in document["users"].items()
                if type(key) is str and _valid_display_preferences(value)}

    def _write_display_preferences(self, rows):
        path = self._display_preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
        payload = json.dumps({"version": 1, "users": rows}, ensure_ascii=False,
                             allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    async def display_preferences(self):
        from .web_api import outcome_response

        try:
            username = self._display_preferences_identity()
            async with self._display_preferences_lock:
                preferences = self._read_display_preferences().get(username)
            return outcome_response(Outcome.success({"preferences": preferences}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except OSError:
            return outcome_response(Outcome.failed(
                "display_preferences_unavailable", "the display preferences could not be read"))

    async def save_display_preferences(self):
        from .web_api import outcome_response

        try:
            username = self._display_preferences_identity()
            raw = await request.body()
            if len(raw) > MAX_DISPLAY_PREFERENCES_BYTES:
                raise ValueError("request is too large")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate field")
                    result[key] = value
                return result

            value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
            if not _valid_display_preferences(value):
                raise ValueError("invalid display preferences")
            preferences = {"appearance": {name: value["appearance"][name]
                                          for name in DISPLAY_APPEARANCE_VALUES},
                           "followLight": value["followLight"],
                           "largeText": value["largeText"]}
            async with self._display_preferences_lock:
                rows = self._read_display_preferences()
                rows[username] = preferences
                self._write_display_preferences(rows)
            return outcome_response(Outcome.success({"preferences": preferences}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed(
                "display_preferences_invalid", "check the display preferences and retry"))
        except OSError:
            return outcome_response(Outcome.failed(
                "display_preferences_unavailable", "the display preferences could not be saved"))

    async def targets(self):
        from .web_api import outcome_response

        try:
            username = request.username
            if type(username) is not str or not username.strip() or username.startswith("api_key:"):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("page_request_invalid", "the page directory does not accept identity overrides")
            async with self.controller._control_lock:
                controller = self.controller
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                if controller.runtime is None or controller.settings is None:
                    raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
                runtime = controller.runtime
                settings_snapshot = controller.settings
                operator = dashboard_operator(runtime, username, at=controller.now())
                require(ObjectAccessPolicy(runtime).feature_access(operator,
                    scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                    action="memory.manage", observed_at=controller.now()))
                inventory = getattr(controller.web_api, "target_inventory", None)
                if callable(inventory):
                    targets = inventory()
                else:
                    settings = controller.resolved_settings()
                    targets = [{"target_id": settings.profile_id, "scope_kind": "private",
                        **{key: getattr(settings, key) for key in ("platform_id", "account_id", "bot_id", "participant_id")},
                        "roles": [{"role_id": settings.role_id, "persona_ids": [settings.persona_id],
                                   **{name + "_api": PREFIX + "/" + name for name in
                                      ("memory", "state", "life", "relationships", "growth", "reply", "model", "config",
                                       # V1 迁移写的是角色设置，所以它的地址跟着角色走；
                                       # 多目标下同一个键由 target_inventory 给每个角色各生成一份。
                                       "legacy",
                                       # 人格工坊按角色挂在同一张目录上：草稿属于「角色 + 宿主人格」，
                                       # 会话窗口重绑不应该让它消失。
                                       "persona")},
                                   "perception_native_api": PREFIX + "/perception-native",
                                   "reality_access_api": PREFIX + "/reality-access",
                                   "screen_access_api": PREFIX + "/screen-access",
                                   "game_access_api": PREFIX + "/game-access",
                                   # QQ 空间拓展按角色安装与授权，所以它的地址也跟角色走。
                                   "qzone_extension_api": PREFIX + "/qzone-extension",
                                   "image_material_api": PREFIX + "/image-materials"}]}]
                # A directory is navigation, not delivery authorization or a configuration dump.
                public = [{key: target.get(key) for key in
                    ("target_id", "scope_kind", "platform_id", "account_id", "bot_id", "participant_id", "group_id",
                     "display_name", "group_name", "account_name")}
                    | {"roles": [{"role_id": role["role_id"],
                                 "role_name": role.get("role_name") or role_display_name(
                                     role["role_id"], role.get("persona_ids", [])),
                                 **({"group_memory_api": role["group_memory_api"]} if role.get("group_memory_api") else {}),
                                 **({"legacy_group_data_api": role["legacy_group_data_api"]}
                                    if role.get("legacy_group_data_api") else {}),
                                 **{name + "_api": role.get(name + "_api") for name in
                                    ("memory", "state", "life", "relationships", "growth", "reply", "model",
                                     "config", "perception_native", "reality_access", "screen_access", "game_access", "persona", "legacy",
                                     "qzone_extension", "image_material")}}
                                 for role in target["roles"]]} for target in targets]
            public = await display_names(controller).targets(GroupRemarks(runtime).apply(public))
            async with controller._control_lock:
                if controller.runtime is not runtime or controller.settings is not settings_snapshot:
                    raise TaskError("staging_not_initialized", "the profile changed while reading display names")
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                require(ObjectAccessPolicy(runtime).feature_access(operator,
                    scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                    action="memory.manage", observed_at=controller.now()))
                return outcome_response(Outcome.success({"installation_id": runtime.installation_id,
                    # Backups cover the whole installation, not one relationship, so the
                    # endpoint sits beside the target list rather than inside a role.
                    "backup_api": PREFIX + "/backup",
                    "acquaintance_api": PREFIX + "/acquaintance",
                    "accounts_api": PREFIX + "/accounts",
                    "operator_id": operator.subject.subject_id, "operator_name": username, "targets": public}))
        except (TaskError, HostControlError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
