"""Authenticated page directory; the host supplies every target and API path."""

import asyncio
import inspect
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from astrbot.api import logger
from astrbot.api.web import json_response, request
from companion_v2.adapters.astrbot.memory_owner import read_memory_companion_status
from companion_v2.execution.models import TaskError, require
from companion_v2.foundation.outcome import Outcome
from companion_v2.foundation.refs import ScopeKind, ScopeRef
from companion_v2.identity_policy.object_access import ObjectAccessPolicy
from companion_v2.storage.runtime import StorageOpenError

from .management_identity import dashboard_operator
from .browser_preferences import (
    BROWSER_PREFERENCE_KEYS,
    BROWSER_PREFERENCE_MAX_AGE,
    MAX_BROWSER_PREFERENCES_BYTES,
    browser_preference_cookie_name,
    encode_browser_preferences,
    read_browser_preferences,
    valid_browser_preference,
)
from .display_names import display_names
from .group_remarks import GroupRemarks
from .identity_derivation import role_display_name
from .search_catalog import build_search_catalog
from .settings import HostControlError


PREFIX = "/astrbot_plugin_private_companion"
DISPLAY_PREFERENCES_FILE = "dashboard_display_preferences.json"
DISPLAY_PREFERENCES_KEYS = frozenset(("appearance", "followLight", "largeText"))
HINT_PREFERENCE_KEYS = frozenset(("memoryCompanion",))
DISPLAY_APPEARANCE_VALUES = {
    "theme": frozenset(("courtyard", "dawn", "dusk", "night", "walnut", "contrast")),
    "density": frozenset(("compact", "standard", "roomy")),
    "radius": frozenset(("sharp", "standard", "round")),
    "typeScale": frozenset(("s", "m", "l", "xl")),
    "motion": frozenset(("full", "low", "off")),
}
MAX_DISPLAY_PREFERENCES_BYTES = 8192
MAX_HINT_PREFERENCES_BYTES = 2048


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


def _valid_hint_preferences(value):
    if type(value) is not dict:
        return False
    return all(
        type(key) is str and key in HINT_PREFERENCE_KEYS and type(flag) is bool
        for key, flag in value.items()
    )


def read_user_hint_preferences(data_root, username: str) -> dict[str, bool]:
    """Read persisted per-Dashboard-user dismissed hint flags (read-only)."""
    defaults = {key: False for key in HINT_PREFERENCE_KEYS}
    if data_root is None or type(username) is not str or not username.strip():
        return defaults
    path = Path(data_root) / DISPLAY_PREFERENCES_FILE
    try:
        raw = path.read_bytes()
    except OSError:
        return defaults
    if raw.startswith(b"\xef\xbb\xbf") or len(raw) > MAX_DISPLAY_PREFERENCES_BYTES * 16:
        return defaults
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return defaults
    if type(document) is not dict or document.get("version") != 1:
        return defaults
    hints = document.get("hints")
    if type(hints) is not dict:
        return defaults
    row = hints.get(username.strip())
    if not _valid_hint_preferences(row):
        return defaults
    return {key: bool(row.get(key, False)) for key in HINT_PREFERENCE_KEYS}


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
                (PREFIX + "/page/memory-companion", self.memory_companion, ["GET"],
                 "Read whether the recommended MemoryCompanion plugin is installed and active"),
                (PREFIX + "/page/display-preferences", self.display_preferences, ["GET"],
                 "Read this Dashboard account's Companion display preferences"),
                (PREFIX + "/page/display-preferences", self.save_display_preferences, ["POST"],
                 "Save this Dashboard account's Companion display preferences"),
                (PREFIX + "/page/hint-preferences", self.hint_preferences, ["GET"],
                 "Read this Dashboard account's dismissed hint preferences"),
                (PREFIX + "/page/hint-preferences", self.save_hint_preferences, ["POST"],
                 "Save this Dashboard account's dismissed hint preferences"),
                (PREFIX + "/page/browser-preferences", self.browser_preferences, ["GET"],
                 "Read this browser's Companion role and card preferences"),
                (PREFIX + "/page/browser-preferences", self.save_browser_preferences, ["POST"],
                 "Save Companion role and card preferences in this browser's cookies"),
                (PREFIX + "/search/catalog", self.search_catalog, ["GET"],
                 "Read searchable model tasks, plugin schema and native extension metadata")]

    async def search_catalog(self):
        """Return non-secret searchable metadata for global search and all-features overview."""
        from .web_api import outcome_response

        try:
            username = request.username
            if (
                type(username) is not str
                or not username.strip()
                or username.startswith("api_key:")
            ):
                raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
            if request.query:
                raise TaskError("page_request_invalid", "search catalog does not accept query overrides")
            if getattr(self.controller, "_terminated", False):
                raise TaskError("staging_host_terminated", "plugin is stopped")
            catalog = await asyncio.to_thread(build_search_catalog, self.controller)
            return outcome_response(Outcome.success(catalog))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

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

    async def memory_companion(self):
        """Read whether 「我会牢牢记住你」 is installed, active, or missing.

        Unlike ``/page/ownership``, this route works even before the V2 profile
        is initialized so onboarding and the home overview can guide new users
        before their first setup completes.
        """
        from .web_api import outcome_response

        try:
            controller = self.controller
            if getattr(controller, "_terminated", False):
                raise TaskError("staging_host_terminated", "plugin is stopped")
            session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
            ownership = None
            if session is not None and getattr(session, "application", None) is not None:
                try:
                    ownership = session.application.memory.context.ownership()
                except Exception:
                    ownership = None
            status = await asyncio.to_thread(
                read_memory_companion_status,
                getattr(controller, "context", None),
                ownership=ownership,
            )
            dismissed = False
            try:
                username = getattr(request, "username", None)
                if (type(username) is str and username.strip()
                        and not username.startswith("api_key:")
                        and getattr(controller, "data_root", None) is not None):
                    async with self._display_preferences_lock:
                        hints = await asyncio.to_thread(
                            read_user_hint_preferences, controller.data_root, username
                        )
                    dismissed = bool(hints.get("memoryCompanion", False))
            except Exception:
                dismissed = False
            return outcome_response(Outcome.success({**status, "hint_dismissed": dismissed}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    def _display_preferences_identity(self):
        username = request.username
        if (type(username) is not str or not username.strip() or username.startswith("api_key:")
                or len(username.strip()) > 128):
            raise TaskError("dashboard_identity_required", "sign in to the AstrBot Dashboard")
        if request.plugin_name != PREFIX.lstrip("/"):
            raise TaskError("page_request_invalid", "page preferences are only available to this plugin")
        if request.query:
            raise TaskError("page_request_invalid", "page preferences do not accept query overrides")
        if getattr(self.controller, "_terminated", False):
            raise TaskError("staging_host_terminated", "plugin is stopped")
        return username.strip()

    def _display_preferences_path(self):
        root = getattr(self.controller, "data_root", None)
        if root is None:
            raise TaskError("display_preferences_unavailable", "the plugin data directory is unavailable")
        return Path(root) / DISPLAY_PREFERENCES_FILE

    def _read_preferences_document(self):
        path = self._display_preferences_path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return {}, {}
        except OSError as error:
            raise TaskError("display_preferences_unavailable",
                            "the display preferences could not be read") from error
        if raw.startswith(b"\xef\xbb\xbf") or len(raw) > MAX_DISPLAY_PREFERENCES_BYTES * 16:
            return {}, {}
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            return {}, {}
        if (type(document) is not dict or document.get("version") != 1
                or type(document.get("users")) is not dict):
            return {}, {}
        users = {key: value for key, value in document["users"].items()
                 if type(key) is str and _valid_display_preferences(value)}
        raw_hints = document.get("hints")
        hints = ({key: {name: bool(val) for name, val in value.items()}
                  for key, value in raw_hints.items()
                  if type(key) is str and _valid_hint_preferences(value)}
                 if type(raw_hints) is dict else {})
        return users, hints

    def _write_preferences_document(self, users, hints):
        path = self._display_preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid4().hex + ".tmp")
        body = {"version": 1, "users": users}
        if hints:
            body["hints"] = hints
        payload = json.dumps(body, ensure_ascii=False,
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

    def _read_display_preferences(self):
        users, _hints = self._read_preferences_document()
        return users

    def _write_display_preferences(self, rows):
        _users, hints = self._read_preferences_document()
        self._write_preferences_document(rows, hints)

    async def hint_preferences(self):
        from .web_api import outcome_response

        try:
            username = self._display_preferences_identity()
            async with self._display_preferences_lock:
                _users, hints = await asyncio.to_thread(self._read_preferences_document)
            row = hints.get(username, {})
            dismissed = {key: bool(row.get(key, False)) for key in HINT_PREFERENCE_KEYS}
            return outcome_response(Outcome.success({"dismissedHints": dismissed}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except OSError:
            return outcome_response(Outcome.failed(
                "display_preferences_unavailable", "the hint preferences could not be read"))

    async def save_hint_preferences(self):
        from .web_api import outcome_response

        try:
            username = self._display_preferences_identity()
            raw = await request.body()
            if len(raw) > MAX_HINT_PREFERENCES_BYTES:
                raise ValueError("request is too large")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate field")
                    result[key] = value
                return result

            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
            if (type(payload) is not dict or set(payload) != {"key", "dismissed"}
                    or type(payload["key"]) is not str or payload["key"] not in HINT_PREFERENCE_KEYS
                    or type(payload["dismissed"]) is not bool):
                raise ValueError("invalid hint preference")
            key, flag = payload["key"], payload["dismissed"]
            async with self._display_preferences_lock:
                def save():
                    users, hints = self._read_preferences_document()
                    current = dict(hints.get(username, {}))
                    current[key] = flag
                    hints[username] = {name: bool(current.get(name, False)) for name in HINT_PREFERENCE_KEYS}
                    self._write_preferences_document(users, hints)
                    return hints[username]

                dismissed = await asyncio.to_thread(save)
            return outcome_response(Outcome.success({"dismissedHints": dismissed}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed(
                "hint_preferences_invalid", "check the hint preferences and retry"))
        except OSError:
            return outcome_response(Outcome.failed(
                "display_preferences_unavailable", "the hint preferences could not be saved"))

    async def display_preferences(self):
        from .web_api import outcome_response

        try:
            username = self._display_preferences_identity()
            async with self._display_preferences_lock:
                preferences = (await asyncio.to_thread(self._read_display_preferences)).get(username)
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
                def save():
                    rows = self._read_display_preferences()
                    rows[username] = preferences
                    self._write_display_preferences(rows)

                await asyncio.to_thread(save)
            return outcome_response(Outcome.success({"preferences": preferences}))
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed(
                "display_preferences_invalid", "check the display preferences and retry"))
        except OSError:
            return outcome_response(Outcome.failed(
                "display_preferences_unavailable", "the display preferences could not be saved"))

    def _browser_preference_cookie_name(self):
        username = self._display_preferences_identity()
        runtime = getattr(self.controller, "runtime", None)
        if runtime is None:
            raise TaskError("staging_not_initialized", "load the explicit V2 profile first")
        return browser_preference_cookie_name(runtime.installation_id, username)

    async def browser_preferences(self):
        from .web_api import outcome_response

        try:
            name = self._browser_preference_cookie_name()
            preferences = read_browser_preferences(request.cookies, name)
            return json_response(outcome_response(Outcome.success({"preferences": preferences})),
                                 headers={"Cache-Control": "no-store"})
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))

    async def save_browser_preferences(self):
        from .web_api import outcome_response

        try:
            name = self._browser_preference_cookie_name()
            raw = await request.body()
            if len(raw) > MAX_BROWSER_PREFERENCES_BYTES:
                raise ValueError("request is too large")
            value = json.loads(raw.decode("utf-8"))
            if (type(value) is not dict or not value or not set(value) <= set(BROWSER_PREFERENCE_KEYS)
                    or any(not valid_browser_preference(key, item) for key, item in value.items())):
                raise ValueError("invalid browser preferences")
            preferences = read_browser_preferences(request.cookies, name) | value
            encoded = encode_browser_preferences(preferences)
            response = json_response(outcome_response(Outcome.success({"preferences": preferences})),
                                     headers={"Cache-Control": "no-store"})
            if all(item is None for item in preferences.values()):
                response.delete_cookie(name, path=request.path, httponly=True, samesite="lax")
            else:
                response.set_cookie(name, encoded, path=request.path,
                                    max_age=BROWSER_PREFERENCE_MAX_AGE,
                                    expires=BROWSER_PREFERENCE_MAX_AGE,
                                    httponly=True, samesite="lax")
            return response
        except TaskError as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            return outcome_response(Outcome.failed(
                "browser_preferences_invalid", "check the browser preferences and retry"))

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
                observed_at = controller.now()
                inventory = getattr(controller.web_api, "target_inventory", None)

            def _accepts_refresh_routes(fn):
                try:
                    sig = inspect.signature(fn)
                except (TypeError, ValueError):
                    return False
                param = sig.parameters.get("refresh_routes")
                if param is not None and param.kind in (
                    inspect.Parameter.KEYWORD_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ):
                    return True
                return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            def prepare_targets():
                operator = dashboard_operator(runtime, username, at=observed_at)
                require(ObjectAccessPolicy(runtime).feature_access(operator,
                    scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                    action="memory.manage", observed_at=observed_at))
                raw_targets = None
                if callable(inventory):
                    targets = (inventory(refresh_routes=False)
                               if _accepts_refresh_routes(inventory) else inventory())
                    raw_targets = targets
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
                return operator, GroupRemarks(runtime).apply(public), raw_targets

            operator, remarked, raw_targets = await asyncio.to_thread(prepare_targets)
            public = await display_names(controller).targets(remarked)
            async with controller._control_lock:
                if controller.runtime is not runtime or controller.settings is not settings_snapshot:
                    raise TaskError("staging_not_initialized", "the profile changed while reading display names")
                if controller._terminated:
                    raise TaskError("staging_host_terminated", "plugin is stopped")
                ensure_routes = getattr(controller.web_api, "_ensure_role_routes", None)
                if callable(ensure_routes) and raw_targets is not None:
                    ensure_routes(raw_targets)
                recheck_at = controller.now()

            def recheck_access():
                require(ObjectAccessPolicy(runtime).feature_access(operator,
                    scope=ScopeRef(runtime.installation_id, "companion", ScopeKind.SYSTEM, "page.management"),
                    action="memory.manage", observed_at=recheck_at))

            await asyncio.to_thread(recheck_access)
            return outcome_response(Outcome.success({"installation_id": runtime.installation_id,
                # Backups cover the whole installation, not one relationship, so the
                # endpoint sits beside the target list rather than inside a role.
                "backup_api": PREFIX + "/backup",
                "acquaintance_api": PREFIX + "/acquaintance",
                "accounts_api": PREFIX + "/accounts",
                "operator_id": operator.subject.subject_id, "operator_name": username, "targets": public}))
        except (TaskError, HostControlError) as error:
            return outcome_response(Outcome.failed(error.code, str(error)))
        except StorageOpenError:
            return outcome_response(Outcome.failed("page_storage_unavailable", "the page directory store is not available"))
