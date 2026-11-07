# -*- coding: utf-8 -*-
"""米家插件的 AstrBot Plugin Pages 后端。

这里仅提供账号授权、配置、同步和诊断能力。为避免 WebUI 变成另一个
“米家遥控器”，本模块刻意不注册任何设备控制或场景执行接口。

``Context.register_web_api`` 注册的路由由 AstrBot Dashboard 统一鉴权；
本模块通过 Dashboard Plugin Pages 桥接提供管理接口。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import io
import json
import re
import time
from copy import deepcopy
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable

from astrbot.api import logger
from astrbot.api.web import json_response, request

from .mihome_device_profiles import (
    CATEGORY_AC,
    CATEGORY_AIR_FRYER,
    CATEGORY_BODY_SCALE,
    CATEGORY_COOKER,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_FAN,
    CATEGORY_GAS_SENSOR,
    CATEGORY_NONE,
    CATEGORY_PURIFIER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_TH_SENSOR,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    VALID_CATEGORIES,
    has_model_profile,
    get_device_detail_actions,
    get_device_detail_readable_keys,
    get_device_detail_writable_keys,
    get_device_action_map,
    get_device_display_map,
    get_device_help_examples,
    get_device_help_hints,
    get_device_prop_map,
    get_device_property_value_map,
    get_device_val_map,
    get_reverse_action_map,
    get_reverse_prop_map,
    resolve_effective_category,
    resolve_profile,
)
from .mihome_client import MiHomeAuthError, MiHomeClientError

PLUGIN_NAME = "astrbot_plugin_private_companion.reality_mihome"

# A parameterised device verb is only ever allowed on the property listed here,
# and only after the device profile itself declares that property writable.
_DEVICE_WRITABLE_PROPERTIES = {
    "set_mode": "mode",
    "set_brightness": "brightness",
    "set_color_temperature": "color_temperature",
}
# 与参考插件一致：这两类动作会把指令转发给别的设备，静态画像再声明也不放行。
DENIED_CROSS_DEVICE_ACTIONS = frozenset({"execute-text-directive", "tv-switchon"})

MAX_MAPPING_COUNT = 500
MAX_ALIAS_LENGTH = 64
MAX_DID_LENGTH = 128
MAX_ERROR_LENGTH = 360

CATEGORY_OPTIONS = (
    CATEGORY_NONE,
    CATEGORY_AC,
    CATEGORY_PURIFIER,
    CATEGORY_FAN,
    CATEGORY_COOKER,
    CATEGORY_AIR_FRYER,
    CATEGORY_TH_SENSOR,
    CATEGORY_BODY_SCALE,
    CATEGORY_VACUUM,
    CATEGORY_WATER_HEATER,
    CATEGORY_ROUTER,
    CATEGORY_SPEAKER,
    CATEGORY_SWITCH,
    CATEGORY_DOOR_SENSOR,
    CATEGORY_GAS_SENSOR,
)

_MAPPING_REVISION_KEYS = (
    "device_map",
    "device_category_map",
    "control_tool",
)
_TOOL_REVISION_KEYS = (
    "scene_tool",
    "control_tool",
    "enable_readonly_tool",
    "enable_scene_tool",
    "scene_tool_admin_only",
    "device_map",
    "device_category_map",
)

_DID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_URL_PATTERN = re.compile(r"https?://[^\s]+", flags=re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)(ticket|serviceToken|passToken|ssecurity|psecurity|nonce|pass_o|"
    r"deviceId|userId|cookie|token|ua)"
    r"([\"']?\s*[:=]\s*[\"']?)([^,\s\"'&}]+)"
)

_LOGIN_ERROR_SCOPE_MARKERS = {
    "credential_storage": (
        "存储路径",
        "文件无法读取",
        "文件安全检查",
        "文件权限",
        "数据目录",
        "临时凭证清理",
        "本地凭证尚未清理",
        "本地登录凭证移除失败",
        "账号缓存清理",
        "状态备份",
    ),
    "authorization": (
        "鉴权失效",
        "鉴权过期",
        "授权失效",
        "凭证失效",
        "凭证过期",
        "登录已过期",
        "login_expired",
        "unauthorized",
        "authentication failed",
    ),
    "cloud_connection": (
        "拉取云端",
        "同步异常",
        "同步设备列表超时",
        "网络异常",
        "连接异常",
        "通信异常",
        "云端接口异常",
        "network_error",
        "ssl_error",
        "cloud_api_error",
        "device_sync_error",
    ),
    "login_flow": (
        "授权确认已超时",
        "未能提取登录链接",
        "二维码",
        "扫码",
        "登录进程",
        "登录任务",
        "登录失败",
        "登录输出",
        "沙盒",
        "qrcode",
    ),
}


class WebAPIError(Exception):
    """可安全返回给管理页面的请求错误。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _redact_text(value: Any, *, limit: int = MAX_ERROR_LENGTH) -> str:
    """移除错误文本中的 URL，防止二维码登录票据意外进入 WebUI。"""

    text = str(value or "").strip()
    if not text:
        return ""
    text = _URL_PATTERN.sub("[链接已隐藏]", text)
    text = _SECRET_PATTERN.sub(r"\1\2[已隐藏]", text)
    text = text.replace("\x00", "")
    if len(text) > limit:
        return f"{text[:limit]}…"
    return text


def _classify_login_error_scope(
    value: Any,
    *,
    credential_present: bool,
) -> str:
    """区分授权、凭证存储、登录流程与云端连接错误。

    ``last_login_error`` 是旧状态字段，历史上也被设备同步的网络错误复用。
    WebUI 只能把明确的鉴权失败标为授权问题，避免把临时网络故障误报为
    “授权异常”。未知错误在没有凭证时按登录流程处理；已有凭证时保留为
    一般账号问题。
    """

    message = str(value or "").strip().casefold()
    if not message:
        return ""
    for scope in (
        "credential_storage",
        "authorization",
        "cloud_connection",
        "login_flow",
    ):
        if any(
            marker.casefold() in message
            for marker in _LOGIN_ERROR_SCOPE_MARKERS[scope]
        ):
            return scope
    return "unknown" if credential_present else "login_flow"


def _credential_state(credential_present: bool, error_scope: str) -> str:
    if error_scope == "credential_storage":
        return "attention"
    if credential_present and error_scope == "authorization":
        return "invalid"
    return "present" if credential_present else "missing"


def _effective_login_error(login: dict[str, Any]) -> str:
    return _redact_text(
        login.get("credential_storage_error")
        or login.get("last_login_error")
    )


def _build_qr_data_uri(content: str) -> str:
    """在本地把登录 URL 编码为 PNG 二维码，不经过第三方服务。

    页面只接受 ``png/jpeg/webp`` 的 data URI；此前这里返回 SVG，尺寸虽小，
    却被前端安全过滤直接丢掉，状态显示「等待扫码确认」但图是空的。
    """

    if not content:
        return ""
    try:
        import qrcode

        image = qrcode.make(content)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception as exc:
        # 异常日志只记录类型，不带可能包含登录 URL 的上下文。
        logger.warning(
            "[MiHome WebUI] 本地生成登录二维码失败: %s",
            type(exc).__name__,
        )
        return ""


def _sensitive_json_response(
    payload: dict[str, Any],
    *,
    status_code: int = 200,
):
    """为可能含短时登录票据的响应显式禁用缓存。"""

    response = json_response(payload, status_code=status_code)
    headers = getattr(response, "headers", None)
    if headers is not None:
        headers["Cache-Control"] = "no-store, max-age=0"
        headers["Pragma"] = "no-cache"
    return response


def _safe_handler(
    handler: Callable[[], Awaitable[Any]],
) -> Callable[[], Awaitable[Any]]:
    """统一处理异常，日志保留细节，响应不泄露底层凭证或文件路径。"""

    @wraps(handler)
    async def wrapper(*_args: Any, **_kwargs: Any):
        try:
            return await handler()
        except asyncio.CancelledError:
            raise
        except WebAPIError as exc:
            return json_response(
                {"ok": False, "error": exc.message},
                status_code=exc.status_code,
            )
        except MiHomeAuthError:
            return json_response(
                {
                    "ok": False,
                    "error": "米家登录已失效，请重新扫码登录。",
                },
                status_code=401,
            )
        except MiHomeClientError as exc:
            logger.warning(
                "[MiHome WebUI] 米家客户端请求失败: %s",
                _redact_text(exc),
            )
            return json_response(
                {
                    "ok": False,
                    "error": "米家云端请求失败，请稍后重试或查看诊断信息。",
                    "detail": _redact_text(exc),
                },
                status_code=502,
            )
        except Exception as exc:
            logger.error(
                "[MiHome WebUI] 接口 %s 执行失败: %s",
                handler.__name__,
                type(exc).__name__,
            )
            return json_response(
                {
                    "ok": False,
                    "error": "插件内部错误，请查看 AstrBot 日志。",
                },
                status_code=500,
            )

    return wrapper


class MiHomeWebAPI:
    """米家 Plugin Page 的受限管理 API。"""

    def __init__(self, plugin: Any):
        self.plugin = plugin
        self._operation_lock = asyncio.Lock()
        self._login_start_lock = asyncio.Lock()
        self._login_task: asyncio.Task | None = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_started_at = ""
        self._login_qr_created_at = 0.0
        self._login_qr_generation = 0
        self._login_qr_revision = ""
        self._login_result: dict[str, Any] = {
            "status": "idle",
            "message": "尚未开始登录",
        }
        self._device_snapshot: list[dict[str, Any]] = []
        self._device_snapshot_at = ""

    def _clear_account_runtime_state(self) -> None:
        self._device_snapshot = []
        self._device_snapshot_at = ""
        self._login_task = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_qr_created_at = 0.0
        self._login_qr_revision = ""
        self._login_started_at = ""
        self._login_result = {
            "status": "idle",
            "message": "已退出米家账号。",
        }

    async def _finish_account_runtime_reset(
        self,
        login_task: asyncio.Task | None,
    ) -> None:
        if (
            login_task is not None
            and login_task is not asyncio.current_task()
            and not login_task.done()
        ):
            login_task.cancel()
            try:
                await login_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "[MiHome WebUI] 登出时回收登录任务异常: %s",
                    type(exc).__name__,
                )
        self._clear_account_runtime_state()

    async def logout_account(self) -> bool:
        """供聊天命令与管理页共用的账号登出协调器。"""

        async with self._login_start_lock:
            async with self._operation_lock:
                login_task = self._login_task
                try:
                    removed = await self._client.logout()
                except MiHomeClientError:
                    client_status = await self._client.get_login_status()
                    if not client_status.get("auth_exists"):
                        await self._finish_account_runtime_reset(login_task)
                    raise
                await self._finish_account_runtime_reset(login_task)
                return bool(removed)

    def extension_snapshot(self) -> dict[str, Any]:
        """Return the small, credential-free surface used by companion plugins."""
        state = self._data_manager.load_state()
        rows = self._cached_device_rows()
        device_map, category_map, mapping_errors = self._mapping_snapshot()
        return {
            "auth": {
                "logged_in": bool(self._data_manager.auth_exists()),
                "login_in_progress": bool(
                    self._login_task is not None and not self._login_task.done()
                ),
                "last_login_at": str(state.get("last_login_at") or ""),
                "last_login_error": _effective_login_error(state),
            },
            "login": self._login_status_payload(),
            "devices": rows,
            "mappings": [
                {"alias": alias, "did": did, "category": category_map.get(alias, CATEGORY_NONE)}
                for alias, did in sorted(device_map.items())
            ],
            "categories": list(CATEGORY_OPTIONS),
            "tool_settings": self._tool_settings(),
            "mapping_errors": mapping_errors,
            "snapshot_at": self._device_snapshot_at,
        }

    async def extension_start_login(self) -> dict[str, Any]:
        """Start the existing QR flow without duplicating login implementation."""
        async with self._login_start_lock:
            if self._login_task is not None and not self._login_task.done():
                return self._login_status_payload()
            async with self._operation_lock:
                client_status = await self._client.get_login_status()
                if client_status.get("auth_exists"):
                    self._login_result = {"status": "already_logged_in", "message": "当前已有米家登录凭证。"}
                    return self._login_status_payload()
                self._login_qr_url = ""
                self._login_qr_image = ""
                self._login_qr_created_at = 0.0
                self._login_qr_revision = ""
                self._login_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._login_result = {"status": "starting", "message": "正在启动安全登录进程…"}
                self._login_task = asyncio.create_task(self._run_login(), name="mihome-companion-login")
                return self._login_status_payload()

    async def extension_login_status(self, known_qr_revision: str = "") -> dict[str, Any]:
        login = await self._client.get_login_status()
        self._reconcile_login_result(login)
        payload = self._login_status_payload(str(known_qr_revision or "")[:64])
        payload.update({
            "credential_present": bool(login.get("auth_exists")),
            "last_login_at": str(login.get("last_login_at") or ""),
            "last_login_error": _effective_login_error(login),
        })
        return payload

    async def extension_sync_devices(self) -> dict[str, Any]:
        self._ensure_operation_available()
        async with self._operation_lock:
            devices = await self._client.get_devices()
            self._device_snapshot = self._compact_device_snapshot(devices)
            self._device_snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 同步是用户已经点过的动作：顺手把没有别名的设备按米家名称补好，
        # 免去逐台打字起名；已有别名与白名单一律不动。
        await self._auto_assign_aliases(devices)
        return self.extension_snapshot()

    async def _auto_assign_aliases(self, devices: list[dict[str, Any]]) -> bool:
        """Give every freshly synced device a default alias from its cloud name.

        Only devices without any mapping are touched; a name already in use gets
        a numeric suffix.  It also fills the category from the trusted model
        profile and allow-lists devices whose profile declares a real power
        switch, so a fresh installation works after one sync.  Nothing already
        configured is overwritten and the whitelist is never widened by hand edits.
        """
        if not isinstance(devices, list) or not devices:
            return False
        device_map, category_map, errors = self._mapping_snapshot()
        if errors:
            return False
        control = self._tool_settings()["control_tool"]
        allowed = list(control["allowed_devices"])
        mapped = set(device_map.values())
        used = set(device_map)
        changed = False
        for item in devices:
            if not isinstance(item, dict):
                continue
            did = str(item.get("did") or "").strip()
            if not did:
                continue
            model = str(item.get("model") or "")
            alias = next((name for name, value in device_map.items() if value == did), "")
            if not alias:
                name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
                base = name[:MAX_ALIAS_LENGTH] or "设备-" + did[-4:]
                alias, index = base, 2
                while alias in used:
                    suffix = " " + str(index)
                    alias = base[: MAX_ALIAS_LENGTH - len(suffix)] + suffix
                    index += 1
                device_map[alias] = did
                used.add(alias)
                mapped.add(did)
                changed = True
            configured = category_map.get(alias, CATEGORY_NONE)
            effective = resolve_effective_category(model=model, category=configured)
            if configured in (None, "", CATEGORY_NONE) and effective != CATEGORY_NONE:
                category_map[alias] = effective
                changed = True
            is_ir = did.startswith("ir.") or model.lower().startswith("miir.")
            writable = get_device_detail_writable_keys(model=model, category=effective)
            if not is_ir and "on" in writable and alias not in allowed:
                allowed.append(alias)
                changed = True
        if not changed:
            return False
        async with self._operation_lock:
            self._config["device_map"] = json.dumps(device_map, ensure_ascii=False, indent=2)
            self._config["device_category_map"] = json.dumps(category_map, ensure_ascii=False, indent=2)
            if allowed != control["allowed_devices"]:
                self._config["control_tool"] = {**control, "enable": True, "allowed_devices": allowed}
            await self._persist_config()
        return True

    async def extension_logout(self) -> dict[str, Any]:
        removed = await self.logout_account()
        return {"credential_removed": bool(removed), "logged_in": False}

    def _is_core_admin(self, actor: Any) -> bool:
        """True only when this exact actor subject holds the core tier."""
        if not isinstance(actor, dict):
            return False
        subject = actor.get("subject") if isinstance(actor.get("subject"), dict) else actor
        subject_id = str(subject.get("subject_id") or "").strip()
        subject_kind = str(subject.get("kind") or "").strip()
        if not subject_id or not subject_kind:
            return False
        try:
            runtime = self.plugin.controller.runtime
            row = runtime.read_one(
                "SELECT tier FROM subject_access_tiers WHERE subject_id = ? AND subject_kind = ?",
                (subject_id, subject_kind))
        except Exception:
            return False
        return bool(row) and str(row["tier"]) == "core"

    def extension_control_preflight(self, alias: str, *, native: bool = False,
                                    actor: Any = None) -> str:
        device_map, _category_map, errors = self._mapping_snapshot()
        if errors:
            raise WebAPIError("米家设备映射配置无效，请先在米家管理中修复。", 409)
        did = device_map.get(alias) if isinstance(alias, str) and alias == alias.strip() else None
        if not did:
            raise WebAPIError("只能控制已配置精确别名的设备。", 404)
        control = self._tool_settings()["control_tool"]
        if not self._config.get("enabled", True) or not control["enable"] or alias not in control["allowed_devices"]:
            raise WebAPIError("当前设备不在已启用的控制白名单中。", 403)
        if native and control["admin_only"] and not self._is_core_admin(actor):
            raise WebAPIError(
                "设备控制只允许核心管理员使用；请由核心管理员本人发起，"
                "或在米家管理页手动操作。",
                403,
            )
        return did

    async def extension_control_power(self, alias: str, is_on: bool, *, native: bool = False,
                                      actor: Any = None) -> dict[str, Any]:
        did = self.extension_control_preflight(alias, native=native, actor=actor)
        confirmed = await self.plugin.client.control_power(did, bool(is_on), alias)
        return {"alias": alias, "is_on": bool(is_on), "confirmed": bool(confirmed)}

    def extension_device_target(self, alias: str, *, native: bool = False,
                                actor: Any = None) -> dict[str, Any]:
        """Return the policy target for one exact alias, without the device ID.

        The DID stays inside this module: callers get the category, whether the
        device is currently allow-listed, and a binding revision derived from
        the alias mapping so a stale reference can be detected after a rebind.
        """
        did = self.extension_control_preflight(alias, native=native, actor=actor)
        device_map, category_map, _ = self._mapping_snapshot()
        row = next((item for item in self._cached_device_rows() if item.get("did") == did), {})
        revision = hashlib.sha256(
            f"{alias}\x1f{did}".encode("utf-8")).hexdigest()[:16]
        return {"alias": alias, "category": category_map.get(alias, CATEGORY_NONE),
                "cached_online": row.get("isOnline"), "binding_revision": revision}

    def _device_profile_context(self, alias: str) -> dict[str, Any]:
        """Resolve one exact alias to the model/category its profile comes from."""
        device_map, category_map, errors = self._mapping_snapshot()
        if errors:
            raise WebAPIError("米家设备映射配置无效，请先在米家管理中修复。", 409)
        if not isinstance(alias, str) or not alias or alias != alias.strip():
            raise WebAPIError("设备别名无效。", 400)
        did = device_map.get(alias)
        if did is None:
            raise WebAPIError("只能控制已配置精确别名的设备。", 404)
        row = next((item for item in self._cached_device_rows() if item.get("did") == did), {})
        model = str(row.get("model") or "")
        configured = category_map.get(alias, CATEGORY_NONE)
        return {
            "alias": alias,
            "did": did,
            "model": model,
            "configured_category": configured,
            "category": resolve_effective_category(model=model, category=configured),
            "is_ir": did.startswith("ir.") or model.lower().startswith("miir."),
            "has_model_profile": bool(has_model_profile(model)),
        }

    def extension_device_profile(self, alias: str) -> dict[str, Any]:
        """Static capability picture: Chinese writable keys, readable keys and actions.

        Mirrors the reference plugin's inspect tool: only what the model/category
        profile itself declares, with the example values used by its help pages.
        Dynamic discovery is deliberately not part of this answer.
        """
        context = self._device_profile_context(alias)
        model, category = context["model"], context["category"]
        writable_keys = get_device_detail_writable_keys(model=model, category=category)
        readable_keys = get_device_detail_readable_keys(model=model, category=category)
        action_keys = [key for key in get_device_detail_actions(model=model, category=category)
                       if key not in DENIED_CROSS_DEVICE_ACTIONS]
        reverse_props = get_reverse_prop_map(model=model, category=category)
        reverse_actions = get_reverse_action_map(model=model, category=category)
        display = get_device_display_map(model=model, category=category)
        examples = get_device_help_examples(model=model, category=category)
        hints = get_device_help_hints(model=model, category=category)
        writable = []
        for key in writable_keys:
            name = reverse_props.get(key, display.get(key, key))
            options = [str(item) for item in examples.get(name, [])]
            hint = str(hints.get(name, ""))
            numeric = key in ("brightness", "color_temperature", "target_temperature") or (
                bool(options) and all(re.fullmatch(r"\d+", item) for item in options))
            bounds = re.search(r"(\d+)\s*[~-]\s*(\d+)", hint)
            low, high = None, None
            if key in ("brightness", "color_temperature"):
                low, high = 1, 100
            elif bounds:
                low, high = int(bounds.group(1)), int(bounds.group(2))
            writable.append({
                "key": key, "name": name, "examples": options, "hint": hint,
                "kind": "number" if numeric else "select" if options else "text",
                "options": [] if numeric else options,
                "minimum": low, "maximum": high,
            })
        actions = [{"key": key, "name": reverse_actions.get(key, key)} for key in action_keys]
        supported = bool(context["has_model_profile"] and not context["is_ir"] and (writable or actions))
        reason = ""
        if context["is_ir"]:
            reason = "红外设备不开放直接控制，请改用米家场景。"
        elif not context["has_model_profile"]:
            reason = "未命中受信任的型号画像；可直接开关电源，其他属性请改用米家场景。"
        elif not writable and not actions:
            reason = "当前型号画像没有可写属性或动作。"
        return {**context, "direct_control_supported": supported,
                "writable_properties": writable,
                "readable_properties": [{"key": key, "name": display.get(key, key)} for key in readable_keys],
                "actions": actions, "unsupported_reason": reason}

    HOME_CARE_ACTIONS = ("power_off", "power_on", "temperature")

    def _home_care_settings(self) -> dict[str, Any]:
        """Opt-in household routines, kept intentionally tiny and auditable.

        A routine is one device, one clock time and one safe action.  It is not
        a general automation editor: the point is to let the companion's
        proactive layer do a couple of ordinary chores (turn the light off at
        bedtime) without turning the plugin into a home-automation hub.
        """
        raw = self._config.get("home_care")
        if not isinstance(raw, dict):
            raw = {}
        routines: list[dict[str, Any]] = []
        for item in raw.get("routines") or []:
            if not isinstance(item, dict):
                continue
            routine_id = str(item.get("id") or "").strip()
            if not re.fullmatch(r"[a-z0-9_]{1,40}", routine_id):
                continue
            clock = str(item.get("time") or "").strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", clock):
                continue
            action = str(item.get("action") or "")
            if action not in self.HOME_CARE_ACTIONS:
                continue
            value: Any = action == "power_on"
            if action == "temperature":
                try:
                    value = int(item.get("value"))
                except (TypeError, ValueError):
                    continue
                if not 16 <= value <= 30:
                    continue
            days: list[int] = []
            for day in item.get("days") or list(range(7)):
                try:
                    day = int(day)
                except (TypeError, ValueError):
                    continue
                if 0 <= day <= 6 and day not in days:
                    days.append(day)
            routines.append({
                "id": routine_id,
                "label": str(item.get("label") or routine_id).strip()[:40] or routine_id,
                "time": clock,
                "days": sorted(days) or list(range(7)),
                "alias": str(item.get("alias") or "").strip()[:MAX_ALIAS_LENGTH],
                "action": action,
                "value": value,
                "enabled": item.get("enabled") is True,
            })
        runs: list[dict[str, Any]] = []
        for item in raw.get("runs") or []:
            if isinstance(item, dict) and isinstance(item.get("at"), str):
                runs.append({
                    "routine_id": str(item.get("routine_id") or "")[:40],
                    "at": item["at"][:40],
                    "marker": str(item.get("marker") or "")[:80],
                    "ok": item.get("ok") is True,
                    "message": str(item.get("message") or "")[:MAX_ERROR_LENGTH],
                })
        return {"enable": raw.get("enable") is True, "routines": routines[:10], "runs": runs[-20:]}

    def extension_home_care_status(self) -> dict[str, Any]:
        home_care = self._home_care_settings()
        control = self._tool_settings()["control_tool"]
        device_map, category_map, errors = self._mapping_snapshot()
        devices = []
        for alias in control["allowed_devices"]:
            did = device_map.get(alias)
            if not did:
                continue
            row = next((item for item in self._cached_device_rows() if item.get("did") == did), {})
            devices.append({"alias": alias, "category": category_map.get(alias, CATEGORY_NONE),
                            "online": row.get("isOnline"), "name": row.get("name") or alias})
        return {**home_care, "available": bool(self._config.get("enabled", True)),
                "control_enabled": control["enable"] is True,
                "devices": devices, "mapping_errors": errors}

    async def extension_home_care_save(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WebAPIError("居家照料配置格式无效。", 400)
        control = self._tool_settings()["control_tool"]
        allowed = set(control["allowed_devices"])
        routines = []
        for item in payload.get("routines") or []:
            if not isinstance(item, dict):
                continue
            routine_id = str(item.get("id") or "").strip()
            if not re.fullmatch(r"[a-z0-9_]{1,40}", routine_id):
                raise WebAPIError("每条照料计划都需要合法的 id。", 400)
            clock = str(item.get("time") or "").strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", clock):
                raise WebAPIError("照料时间需要 HH:MM 格式。", 400)
            action = str(item.get("action") or "")
            if action not in self.HOME_CARE_ACTIONS:
                raise WebAPIError("不支持的照料动作。", 400)
            alias = str(item.get("alias") or "").strip()
            if alias not in allowed:
                raise WebAPIError("只能为已允许控制的设备安排照料计划。", 400)
            value: Any = action == "power_on"
            if action == "temperature":
                try:
                    value = int(item.get("value"))
                except (TypeError, ValueError):
                    raise WebAPIError("温度需要整数取值。", 400)
                if not 16 <= value <= 30:
                    raise WebAPIError("温度需要在 16~30 之间。", 400)
            days = sorted({int(day) for day in item.get("days") or list(range(7))
                           if str(day).lstrip("-").isdigit() and 0 <= int(day) <= 6})
            routines.append({
                "id": routine_id, "label": str(item.get("label") or routine_id)[:40],
                "time": clock, "days": days or list(range(7)), "alias": alias,
                "action": action, "value": value, "enabled": item.get("enabled") is True,
            })
        if len(routines) > 10:
            raise WebAPIError("最多安排 10 条居家照料计划。", 400)
        current = self._home_care_settings()
        async with self._operation_lock:
            self._config["home_care"] = {"enable": payload.get("enable") is True,
                                         "routines": routines, "runs": current["runs"]}
            await self._persist_config()
        return self.extension_home_care_status()

    async def _execute_home_care(self, routine: dict[str, Any]) -> dict[str, Any]:
        alias = routine["alias"]
        control = self._tool_settings()["control_tool"]
        if not control["enable"] or alias not in control["allowed_devices"]:
            raise WebAPIError("设备控制未启用，或这台设备已不在白名单。", 403)
        if routine["action"] == "temperature":
            return await self.extension_device_action(alias, "target_temperature",
                                                      str(routine["value"]), native=False)
        return await self.extension_control_power(alias, routine["action"] == "power_on",
                                                  native=False)

    async def extension_home_care_run(self, routine_id: str) -> dict[str, Any]:
        home_care = self._home_care_settings()
        routine = next((item for item in home_care["routines"] if item["id"] == routine_id), None)
        if routine is None:
            raise WebAPIError("没有找到这条照料计划。", 404)
        at = datetime.now()
        try:
            receipt = await self._execute_home_care(routine)
            ok = bool(receipt.get("confirmed"))
            message = "已执行" if ok else "设备未确认"
        except WebAPIError:
            raise
        except Exception as exc:
            ok, message = False, str(exc)[:MAX_ERROR_LENGTH]
        home_care["runs"] = (home_care["runs"] + [{
            "routine_id": routine_id, "at": at.isoformat(timespec="seconds"),
            "marker": f"manual:{routine_id}:{at.isoformat(timespec='seconds')}",
            "ok": ok, "message": message,
        }])[-20:]
        async with self._operation_lock:
            self._config["home_care"] = home_care
            await self._persist_config()
        return {**self.extension_home_care_status(), "result": {"ok": ok, "message": message}}

    async def extension_home_care_tick(self, now: datetime | None = None) -> dict[str, Any]:
        """Run due routines once per local day; called by the reality runtime loop."""
        home_care = self._home_care_settings()
        if not home_care["enable"] or not home_care["routines"]:
            return {"ran": 0}
        current = now or datetime.now()
        clock = current.strftime("%H:%M")
        today = current.strftime("%Y-%m-%d")
        ran = 0
        for routine in home_care["routines"]:
            if not routine["enabled"] or current.weekday() not in routine["days"]:
                continue
            if routine["time"] != clock:
                continue
            marker = f"{routine['id']}:{today}"
            if any(run.get("marker") == marker for run in home_care["runs"]):
                continue
            try:
                receipt = await self._execute_home_care(routine)
                ok = bool(receipt.get("confirmed"))
                message = "已执行" if ok else "设备未确认"
            except Exception as exc:
                ok, message = False, str(exc)[:MAX_ERROR_LENGTH]
            home_care["runs"] = (home_care["runs"] + [{
                "routine_id": routine["id"], "at": current.isoformat(timespec="seconds"),
                "marker": marker, "ok": ok, "message": message,
            }])[-20:]
            ran += 1
            if ran >= 3:
                break
        if ran:
            async with self._operation_lock:
                self._config["home_care"] = home_care
                await self._persist_config()
        return {"ran": ran}

    def _care_settings(self) -> dict[str, Any]:
        from companion_v2.proactive.care import normalize_settings
        return normalize_settings(self._config.get("care"))

    def extension_care_status(self) -> dict[str, Any]:
        from companion_v2.proactive.care import ACTIONS, CONCERNS
        settings = self._care_settings()
        _device_map, category_map, _errors = self._mapping_snapshot()
        control = self._tool_settings()["control_tool"]
        devices = [{"alias": alias, "category": category_map.get(alias, CATEGORY_NONE)}
                   for alias in control["allowed_devices"]]
        learning = {}
        for item in CONCERNS:
            rows = [entry for entry in settings["feedback"] if entry["concern_id"] == item.id]
            praise = sum(1 for entry in rows if entry["verdict"] == "praise")
            pause = sum(1 for entry in rows if entry["verdict"] == "pause")
            learning[item.id] = {"praise": praise, "pause": pause, "trust": praise - pause}
        return {**settings,
                "concerns_meta": [{"id": item.id, "label": item.label, "note": item.note,
                                   "weight": item.weight, "supported": item.supported,
                                   "cue": item.cue} for item in CONCERNS],
                "learning": learning,
                "action_meta": [{"id": key, "risk": meta["risk"], "label": meta["label"],
                                 "description": meta["description"],
                                 "default_enabled": meta["default_enabled"] is True}
                                for key, meta in ACTIONS.items()],
                "devices": devices, "available": bool(self._config.get("enabled", True)),
                "control_enabled": control["enable"] is True}

    async def extension_care_tick(self, now: datetime | None = None) -> dict[str, Any]:
        """Observe devices and record what the character would notice; never act."""
        from companion_v2.proactive.care import DeviceObservation, appraise
        settings = self._care_settings()
        if not settings["enable"]:
            return {"observed": 0, "impulses": []}
        control = self._tool_settings()["control_tool"]
        if not control["enable"]:
            return {"observed": 0, "impulses": []}
        current = now or datetime.now()
        observations = []
        for alias in control["allowed_devices"]:
            try:
                status = await self.extension_read_device_status(alias)
            except Exception:
                continue
            if not isinstance(status, dict) or status.get("available") is not True:
                continue
            row = next((item for item in self._cached_device_rows()
                        if alias in (item.get("aliases") or [])), {})
            observations.append(DeviceObservation(
                alias=alias,
                category=str(row.get("category") or self._care_category(alias)),
                online=row.get("isOnline"),
                state=status.get("properties") or {},
            ))
        impulses = appraise(settings, observations, current)
        known = {}
        for index, item in enumerate(settings["impulses"]):
            known.setdefault(item.get("marker"), index)
        added: list[dict[str, Any]] = []
        changed = False
        today = current.date().isoformat()
        counters = settings["counters"]
        if counters.get("date") != today:
            counters = {"date": today,
                        "by_action": {key: 0 for key in settings["actions"]}}
            changed = True
        seen = getattr(self, "_care_seen", None)
        if not isinstance(seen, dict):
            seen = self._care_seen = {}
        for impulse in impulses:
            marker = impulse.get("marker")
            existing = known.get(marker)
            acted = False
            concern_id = impulse.get("concern_id")
            action_config = settings["actions"].get(concern_id) or {}
            concern = settings["concerns"].get(concern_id) or {}
            if impulse.get("action") and action_config.get("enabled") is True \
                    and concern.get("enabled") is True:
                key = f"{concern_id}:{impulse['alias']}"
                first = seen.get(key)
                if first is None:
                    seen[key] = current.isoformat(timespec="seconds")
                    impulse["reason"] = "它先看着，想再等一会儿，还没有动手。"
                else:
                    try:
                        waited = int((current - datetime.fromisoformat(first)).total_seconds() // 60)
                    except (TypeError, ValueError):
                        waited = 0
                    if waited >= int(action_config["patience_minutes"]) \
                            and int(counters["by_action"].get(concern_id, 0)) < int(action_config["daily_cap"]):
                        try:
                            if concern_id == "ac_temperature":
                                observed = next((item for item in observations
                                                 if item.alias == impulse["alias"]), None)
                                try:
                                    current_target = int(observed.state.get("target_temperature")) \
                                        if observed is not None else 24
                                except (TypeError, ValueError):
                                    current_target = 24
                                warm_target = min(24, current_target + 2)
                                receipt = await self.extension_device_action(
                                    impulse["alias"], "target_temperature", str(warm_target),
                                    native=False)
                            else:
                                receipt = await self.extension_control_power(
                                    impulse["alias"], False, native=False)
                            confirmed = bool(receipt.get("confirmed"))
                        except Exception as exc:
                            confirmed = False
                            impulse["reason"] = f"它想帮忙，但没有成功：{str(exc)[:120]}"
                        if confirmed:
                            impulse["verdict"] = "acted"
                            if concern_id == "ac_temperature":
                                impulse["reason"] = (f"温度偏低已经 {waited} 分钟，"
                                                     "它把空调往舒适方向调了一点，没有出声。")
                            else:
                                impulse["reason"] = (f"灯亮了 {waited} 分钟，你大概已经睡了；"
                                                     "它把灯关掉了，没有出声。")
                            counters["by_action"][concern_id] = int(
                                counters["by_action"].get(concern_id, 0)) + 1
                            seen.pop(key, None)
                            acted = True
                            changed = True
            if existing is not None:
                if acted:
                    settings["impulses"][existing] = impulse
                continue
            added.append(impulse)
        active = {f"{item['concern_id']}:{item['alias']}" for item in impulses}
        for key in list(seen):
            if key not in active:
                seen.pop(key, None)
        if added:
            settings["impulses"] = (settings["impulses"] + added)[-40:]
            changed = True
        settings["counters"] = counters
        if changed:
            async with self._operation_lock:
                self._config["care"] = settings
                await self._persist_config()
        return {"observed": len(observations), "impulses": added +
                [item for item in impulses if item.get("verdict") == "acted"]}

    def _care_category(self, alias: str) -> str:
        _device_map, category_map, _errors = self._mapping_snapshot()
        category = category_map.get(alias, CATEGORY_NONE)
        return "" if category == CATEGORY_NONE else category

    async def extension_care_feedback(self, concern_id: str, verdict: str) -> dict[str, Any]:
        from companion_v2.proactive.care import CONCERNS
        if concern_id not in {item.id for item in CONCERNS}:
            raise WebAPIError("未知的在意项。", 400)
        if verdict not in {"allow", "pause", "praise"}:
            raise WebAPIError("未知的纠正方式。", 400)
        settings = self._care_settings()
        item = settings["concerns"].setdefault(
            concern_id, {"enabled": False, "weight": "medium", "note": ""})
        action = settings["actions"].get(concern_id)
        settings["feedback"] = (settings["feedback"] + [{
            "concern_id": concern_id, "verdict": verdict,
            "at": datetime.now().isoformat(timespec="seconds")}])[-30:]
        if verdict == "allow":
            item["enabled"] = True
            item["weight"] = "high"
        elif verdict == "pause":
            item["enabled"] = False
            item["weight"] = "low"
            if action is not None:
                action["enabled"] = False
        else:
            item["weight"] = "high"
            if action is not None:
                action["patience_minutes"] = max(15, int(action["patience_minutes"]) - 5)
        async with self._operation_lock:
            self._config["care"] = settings
            await self._persist_config()
        return self.extension_care_status()

    async def extension_care_save(self, payload: Any) -> dict[str, Any]:
        from companion_v2.proactive.care import normalize_settings
        if not isinstance(payload, dict):
            raise WebAPIError("在意设置格式无效。", 400)
        current = self._care_settings()
        settings = normalize_settings({"enable": payload.get("enable") is True,
                                       "concerns": payload.get("concerns") or {},
                                       "actions": payload.get("actions") or {},
                                       "counters": current["counters"],
                                       "feedback": current["feedback"],
                                       "impulses": current["impulses"]})
        async with self._operation_lock:
            self._config["care"] = settings
            await self._persist_config()
        return self.extension_care_status()


    def _device_value(self, *, model: str, category: str, key: str, value: Any) -> Any:
        """Fold the Chinese value from a profile into what the cloud property wants."""
        if isinstance(value, bool):
            return value
        text = "" if value is None else str(value).strip()
        if not text or len(text) > 256:
            raise WebAPIError("该属性取值无效。", 400)
        property_map = get_device_property_value_map(model=model, category=category, property_key=key)
        if text in property_map:
            return property_map[text]
        value_map = get_device_val_map(model=model, category=category)
        if text in value_map:
            return value_map[text]
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        return text

    def extension_device_actions(self, alias: str) -> dict[str, Any]:
        """List the verbs and profile action names this exact device declares."""
        directory = self.extension_device_directory()
        profile = self.extension_device_profile(alias)
        writable = {item["key"] for item in profile["writable_properties"]}
        actions = ["power"]
        for verb, prop in (("set_mode", "mode"), ("set_brightness", "brightness"),
                           ("set_color_temperature", "color_temperature")):
            if prop in writable:
                actions.append(verb)
        return {"alias": alias, "actions": actions,
                "profile_properties": [item["name"] for item in profile["writable_properties"]],
                "profile_actions": [item["name"] for item in profile["actions"]],
                "direct_control_supported": profile["direct_control_supported"],
                "unsupported_reason": profile["unsupported_reason"],
                "logged_in": bool(directory.get("logged_in"))}

    async def extension_device_action(self, alias: str, action: str, value: Any, *,
                                      native: bool = False,
                                      actor: Any = None,
                                      binding_revision: str = "") -> dict[str, Any]:
        """Run one declared device verb, refusing anything the profile does not allow."""
        if not isinstance(action, str) or not action.strip() or len(action) > 64:
            raise WebAPIError("该设备动作不在允许的动作集合内。", 400)
        action = action.strip()
        if action not in _DEVICE_WRITABLE_PROPERTIES and action != "power":
            # 参考插件的「属性/动作」控制：中文名由设备画像换算成底层 key。
            return await self._extension_profile_action(alias, action, value, native=native,
                                                        actor=actor,
                                                        binding_revision=binding_revision)
        target = self.extension_device_target(alias, native=native, actor=actor)
        if binding_revision and str(binding_revision) != target["binding_revision"]:
            # The alias was rebound after this request was prepared.  The old
            # reference must not silently follow the new device.
            raise WebAPIError("设备绑定已变更，请按当前绑定重新发起。", 409)
        if action == "power":
            if not isinstance(value, bool):
                raise WebAPIError("电源动作需要布尔取值。", 400)
            return await self.extension_control_power(alias, value, native=native, actor=actor)
        prop = _DEVICE_WRITABLE_PROPERTIES[action]
        allowed = self.extension_device_actions(alias)["actions"]
        if action not in allowed:
            raise WebAPIError("该设备档案未声明此属性可写。", 403)
        if prop in ("brightness", "color_temperature"):
            if not isinstance(value, int) or isinstance(value, bool):
                raise WebAPIError("该属性需要整数取值。", 400)
            number = int(value)
            if not 1 <= number <= 100:
                raise WebAPIError("该属性取值需要在 1~100 之间。", 400)
            value = number
        elif prop == "mode":
            if not isinstance(value, str) or not value.strip() or len(value) > 60:
                raise WebAPIError("该属性需要字符串取值。", 400)
            value = value.strip()
        did = self.extension_control_preflight(alias, native=native, actor=actor)
        self._ensure_operation_available()
        async with self._operation_lock:
            confirmed = await self.plugin.client.set_property(did, prop, value, alias)
        return {"alias": alias, "action": action, "property": prop,
                "value": value, "confirmed": bool(confirmed)}

    async def _extension_profile_action(self, alias: str, action: str, value: Any, *,
                                        native: bool = False,
                                        actor: Any = None,
                                        binding_revision: str = "") -> dict[str, Any]:
        """Resolve one Chinese property/action name through the device profile.

        The model never sends a raw property key: it sends the name shown by
        ``reality.device.inspect`` and this method folds it through the profile's
        reverse maps. A name the profile does not declare is refused before any
        network call, exactly like the reference plugin's control tool.
        """
        context = self._device_profile_context(alias)
        model, category = context["model"], context["category"]
        prop_map = get_device_prop_map(model=model, category=category)
        action_map = get_device_action_map(model=model, category=category)
        writable = get_device_detail_writable_keys(model=model, category=category)
        target = self.extension_device_target(alias, native=native, actor=actor)
        if binding_revision and str(binding_revision) != target["binding_revision"]:
            raise WebAPIError("设备绑定已变更，请按当前绑定重新发起。", 409)
        key = prop_map.get(action, action if action in writable else None)
        if key is not None:
            if key not in writable:
                raise WebAPIError("该设备档案未声明此属性可写。", 403)
            folded = self._device_value(model=model, category=category, key=key, value=value)
            did = self.extension_control_preflight(alias, native=native, actor=actor)
            self._ensure_operation_available()
            async with self._operation_lock:
                confirmed = await self.plugin.client.set_property(did, key, folded, alias)
            return {"alias": alias, "action": action, "property": key,
                    "value": folded, "confirmed": bool(confirmed)}
        key = action_map.get(action, action)
        declared = [item for item in get_device_detail_actions(model=model, category=category)
                    if item not in DENIED_CROSS_DEVICE_ACTIONS]
        if key not in declared:
            raise WebAPIError("该设备档案没有声明这个动作。", 403)
        if not context["has_model_profile"]:
            raise WebAPIError("直接控制动作需要精确型号画像；请改用米家场景。", 403)
        did = self.extension_control_preflight(alias, native=native, actor=actor)
        self._ensure_operation_available()
        async with self._operation_lock:
            confirmed = await self.plugin.client.run_action(did, key, alias)
        return {"alias": alias, "action": action, "key": key, "confirmed": bool(confirmed)}

    def extension_device_directory(self) -> dict[str, Any]:
        """Return only mapped aliases and policy hints to the V2 model."""
        if not self._config.get("enabled", True) or not self._tool_settings()["enable_readonly_tool"]:
            raise WebAPIError("米家只读查询未启用。", 403)
        device_map, category_map, errors = self._mapping_snapshot()
        if errors:
            raise WebAPIError("米家设备映射配置无效，请先在米家管理中修复。", 409)
        rows = {row["did"]: row for row in self._cached_device_rows()}
        control = self._tool_settings()["control_tool"]
        devices = []
        for alias, did in sorted(device_map.items())[:100]:
            row = rows.get(did, {})
            devices.append({
                "alias": alias,
                "category": resolve_effective_category(
                    model=str(row.get("model") or ""),
                    category=category_map.get(alias, CATEGORY_NONE),
                ),
                "cached_online": row.get("online") if type(row.get("online")) is bool else None,
                "control_allowlisted": bool(control["enable"] and alias in control["allowed_devices"]),
                "control_requires_admin": bool(control["admin_only"]),
            })
        return {"devices": devices, "more_devices": max(0, len(device_map) - len(devices)),
                "cache_updated_at": self._device_snapshot_at,
                "logged_in": bool(self._data_manager.auth_exists())}

    async def extension_read_device_status(self, alias: str) -> dict[str, Any]:
        """Read a bounded set of profile properties for one exact mapped alias."""
        if not isinstance(alias, str) or not alias or alias != alias.strip():
            raise WebAPIError("设备别名无效。", 400)
        directory = self.extension_device_directory()
        device_map, category_map, _ = self._mapping_snapshot()
        did = device_map.get(alias)
        if did is None:
            raise WebAPIError("只能读取已配置精确别名的设备。", 404)
        if not directory["logged_in"]:
            return {"alias": alias, "available": False, "reason": "not_logged_in", "properties": {}}
        self._ensure_operation_available()
        row = next((item for item in self._cached_device_rows() if item["did"] == did), {})
        model = str(row.get("model") or "")
        category = category_map.get(alias, CATEGORY_NONE)
        keys = list(dict.fromkeys([
            *get_device_detail_writable_keys(model=model, category=category),
            *get_device_detail_readable_keys(model=model, category=category),
        ]))[:32]
        if not keys:
            keys = ["on"]
        async with self._operation_lock:
            result = await self._client.get_device_props(did, keys)
        if not isinstance(result, dict) or result.get("__error__"):
            return {"alias": alias, "available": False, "reason": "device_query_failed", "properties": {}}
        readable = result.get("readable")
        if not isinstance(readable, dict):
            readable = {}
        properties = {
            key: _redact_text(readable[key], limit=120).replace("\n", " ").replace("\r", " ")
            for key in keys if key in readable and isinstance(readable[key], (str, int, float, bool))
        }
        return {"alias": alias, "available": bool(properties),
                "reason": "" if properties else "no_readable_properties", "properties": properties}

    async def extension_save_mappings(self, mappings: Any) -> dict[str, Any]:
        """Persist the small alias-to-DID surface used by companion pages."""
        if not isinstance(mappings, list):
            raise WebAPIError("设备映射必须是数组。")
        new_device_map, new_category_map = self._parse_mapping_rows({"mappings": mappings})
        known_devices = {row["did"] for row in self._cached_device_rows() if not row.get("missing_from_cloud")}
        old_device_map, old_category_map, _ = self._mapping_snapshot()
        unknown_dids = sorted(set(new_device_map.values()) - known_devices - set(old_device_map.values()))
        if known_devices and unknown_dids:
            raise WebAPIError("设备 DID 不在最近同步清单中。")
        current_control = self._tool_settings()["control_tool"]
        removed = set(old_device_map) - set(new_device_map)
        async with self._operation_lock:
            self._config["device_map"] = json.dumps(new_device_map, ensure_ascii=False, indent=2)
            self._config["device_category_map"] = json.dumps(new_category_map, ensure_ascii=False, indent=2)
            self._config["control_tool"] = {
                **current_control,
                "allowed_devices": [alias for alias in current_control["allowed_devices"] if alias not in removed],
            }
            await self._persist_config()
        return self.extension_snapshot()

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------

    def register_routes(self, context: Any) -> None:
        """注册由 AstrBot Dashboard 统一鉴权的页面接口。"""

        def register(
            method: str,
            path: str,
            handler: Callable[[], Awaitable[Any]],
            description: str,
        ) -> None:
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}",
                _safe_handler(handler),
                [method],
                description,
            )

        register("GET", "/status", self.get_status, "米家 WebUI：概览")
        register("POST", "/auth/start", self.start_login, "米家 WebUI：开始扫码登录")
        register(
            "GET", "/auth/status", self.get_auth_status, "米家 WebUI：登录流程状态"
        )
        register("POST", "/auth/logout", self.logout, "米家 WebUI：退出登录")
        register("GET", "/devices", self.get_devices, "米家 WebUI：设备清单")
        register("POST", "/devices/sync", self.sync_devices, "米家 WebUI：同步设备")
        register(
            "POST",
            "/devices/mappings",
            self.save_device_mappings,
            "米家 WebUI：保存设备映射",
        )
        register(
            "GET",
            "/devices/status",
            self.get_readonly_device_status,
            "米家 WebUI：读取已配置设备状态",
        )
        register("GET", "/scenes", self.get_scenes, "米家 WebUI：场景缓存")
        register("POST", "/scenes/sync", self.sync_scenes, "米家 WebUI：同步场景")
        register("GET", "/tools", self.get_tool_settings, "米家 WebUI：Tool 设置")
        register(
            "POST", "/tools", self.save_tool_settings, "米家 WebUI：保存 Tool 设置"
        )
        register("GET", "/diagnostics", self.get_diagnostics, "米家 WebUI：诊断")
        register(
            "POST",
            "/diagnostics/check",
            self.run_diagnostics,
            "米家 WebUI：重新诊断",
        )

    async def shutdown(self) -> None:
        """停止页面后台任务，不保留二维码登录票据。"""

        task = self._login_task
        if task is not None and not task.done():
            # 先终止登录子进程，再取消包装任务，避免遗留沙箱进程。
            terminator = getattr(self._client, "terminate", None)
            if callable(terminator):
                try:
                    result = terminator()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning(
                        "[MiHome WebUI] 停止登录子进程失败: %s",
                        exc,
                    )
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("[MiHome WebUI] 登录任务结束异常: %s", exc)

        self._login_task = None
        self._login_qr_url = ""
        self._login_qr_image = ""
        self._login_qr_created_at = 0.0
        self._login_qr_revision = ""
        self._login_started_at = ""
        self._login_result = {
            "status": "idle",
            "message": "登录流程已停止。",
        }

    async def terminate(self) -> None:
        """兼容插件生命周期中常用的 terminate 命名。"""

        await self.shutdown()

    # ------------------------------------------------------------------
    # 通用辅助
    # ------------------------------------------------------------------

    @property
    def _config(self) -> Any:
        return self.plugin.mihome_config

    @property
    def _client(self) -> Any:
        return self.plugin.client

    @property
    def _data_manager(self) -> Any:
        return self.plugin.data_manager

    async def _read_json_body(self) -> dict[str, Any]:
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            raise WebAPIError("请求体必须是 JSON 对象。")
        return payload

    async def _persist_config(self) -> None:
        """兼容 AstrBotConfig 与少量旧版插件保存方式。"""

        saver = getattr(self._config, "save_config", None)
        if not callable(saver):
            saver = getattr(self.plugin, "save_config", None)
        if not callable(saver):
            saver = getattr(getattr(self.plugin, "config", None), "save_config", None)
        if not callable(saver):
            raise RuntimeError("当前 AstrBotConfig 不支持持久化")
        result = saver()
        if inspect.isawaitable(result):
            await result

    def _config_revision(self, keys: tuple[str, ...]) -> str:
        """为 WebUI 管理的配置生成稳定版本，用于阻止陈旧页面覆盖新设置。"""

        snapshot = {
            key: (
                {"present": True, "value": deepcopy(self._config[key])}
                if key in self._config
                else {"present": False}
            )
            for key in keys
        }
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _expected_revision(self, data: dict[str, Any]) -> str:
        revision = data.get("revision")
        if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
            raise WebAPIError("配置版本无效，请刷新页面后重试。", 409)
        return revision

    def _ensure_revision(self, expected: str, keys: tuple[str, ...]) -> None:
        current = self._config_revision(keys)
        if not hmac.compare_digest(expected, current):
            raise WebAPIError(
                "配置已由 AstrBot 插件设置或其他管理页面更新，请刷新后重试。",
                409,
            )

    def _ensure_operation_available(
        self,
        *,
        allow_login_task: bool = False,
    ) -> None:
        if self._operation_lock.locked():
            raise WebAPIError("已有管理操作正在执行，请稍后再试。", 409)
        if (
            not allow_login_task
            and self._login_task is not None
            and not self._login_task.done()
        ):
            raise WebAPIError(
                "扫码登录正在进行，完成或退出登录后再执行此操作。",
                409,
            )

    def _parse_config_map(self, key: str) -> tuple[dict[str, str], str]:
        raw = self._config.get(key, "{}")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, f"{key} 不是合法 JSON"
        if not isinstance(parsed, dict):
            return {}, f"{key} 必须是 JSON 对象"

        normalized: dict[str, str] = {}
        for raw_key, raw_value in parsed.items():
            key_text = str(raw_key).strip()
            if not key_text:
                continue
            normalized[key_text] = str(raw_value).strip()
        return normalized, ""

    def _mapping_snapshot(
        self,
    ) -> tuple[dict[str, str], dict[str, str], list[str]]:
        device_map, device_error = self._parse_config_map("device_map")
        category_map, category_error = self._parse_config_map("device_category_map")
        errors = [item for item in (device_error, category_error) if item]
        return device_map, category_map, errors

    def _cached_device_rows(
        self,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if self._device_snapshot:
            return self._build_device_rows(self._device_snapshot, state)

        if state is None:
            state = self._data_manager.load_state()
        did_to_name = state.get("did_to_name", {})
        did_to_model = state.get("did_to_model", {})
        if not isinstance(did_to_name, dict):
            did_to_name = {}
        if not isinstance(did_to_model, dict):
            did_to_model = {}

        cached = []
        all_dids = dict.fromkeys(
            [str(key) for key in did_to_name] + [str(key) for key in did_to_model]
        )
        for did in all_dids:
            cached.append(
                {
                    "did": did,
                    "name": did_to_name.get(did, "未知设备"),
                    "model": did_to_model.get(did, ""),
                    "isOnline": None,
                }
            )
        return self._build_device_rows(cached, state)

    def _build_device_rows(
        self,
        devices: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        device_map, category_map, _ = self._mapping_snapshot()
        aliases_by_did: dict[str, list[str]] = {}
        for alias, did in device_map.items():
            aliases_by_did.setdefault(did, []).append(alias)

        rows: list[dict[str, Any]] = []
        seen_dids: set[str] = set()
        if state is None:
            state = self._data_manager.load_state()
        cached_names = state.get("did_to_name", {})
        cached_models = state.get("did_to_model", {})
        if not isinstance(cached_names, dict):
            cached_names = {}
        if not isinstance(cached_models, dict):
            cached_models = {}
        for item in devices:
            if not isinstance(item, dict):
                continue
            did = str(item.get("did") or "").strip()
            if not did or did in seen_dids:
                continue
            seen_dids.add(did)
            name = str(item.get("name") or "未知设备").strip() or "未知设备"
            model = str(item.get("model") or "").strip()
            online = item.get("isOnline")
            if not isinstance(online, bool):
                online = None
            shared = item.get(
                "isShared",
                item.get("is_shared", item.get("shared")),
            )
            if not isinstance(shared, bool):
                shared = None

            aliases = sorted(aliases_by_did.get(did, []))
            mappings = []
            for alias in aliases:
                configured = category_map.get(alias, CATEGORY_NONE)
                effective = resolve_effective_category(
                    model=model,
                    category=configured,
                )
                mappings.append(
                    {
                        "alias": alias,
                        "category": configured
                        if configured in VALID_CATEGORIES
                        else CATEGORY_NONE,
                        "effective_category": effective,
                    }
                )

            rows.append(
                {
                    "did": did,
                    "name": name,
                    "model": model,
                    "online": online,
                    "shared": shared,
                    "configured": bool(aliases),
                    "aliases": aliases,
                    "mappings": mappings,
                    "profile_matched": has_model_profile(model),
                }
            )

        # 仍展示不在当前云端快照中的旧映射，便于管理员修复。
        for did, aliases in aliases_by_did.items():
            if did in seen_dids:
                continue
            name = str(cached_names.get(did) or "未在当前设备列表中").strip()
            model = str(cached_models.get(did) or "").strip()
            mappings = []
            for alias in sorted(aliases):
                configured = category_map.get(alias, CATEGORY_NONE)
                mappings.append(
                    {
                        "alias": alias,
                        "category": configured
                        if configured in VALID_CATEGORIES
                        else CATEGORY_NONE,
                        "effective_category": resolve_effective_category(
                            model=model,
                            category=configured,
                        ),
                    }
                )
            rows.append(
                {
                    "did": did,
                    "name": name,
                    "model": model,
                    "online": None,
                    "shared": None,
                    "configured": True,
                    "aliases": sorted(aliases),
                    "mappings": mappings,
                    "profile_matched": has_model_profile(model),
                    "missing_from_cloud": True,
                }
            )

        rows.sort(key=lambda row: (not row["configured"], row["name"], row["did"]))
        return rows

    @staticmethod
    def _compact_device_snapshot(
        devices: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        snapshot = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            snapshot.append(
                {
                    "did": item.get("did"),
                    "name": item.get("name"),
                    "model": item.get("model"),
                    "isOnline": item.get("isOnline"),
                    "isShared": item.get(
                        "isShared",
                        item.get("is_shared", item.get("shared")),
                    ),
                }
            )
        return snapshot

    def _sanitized_scenes(
        self,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        if state is None:
            state = self._data_manager.load_state()
        scenes = state.get("scenes", [])
        if not isinstance(scenes, list):
            return []
        result = []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            result.append(
                {
                    "scene_id": str(scene.get("scene_id") or "").strip(),
                    "scene_name": str(scene.get("scene_name") or "未命名场景").strip(),
                    "home_id": str(scene.get("home_id") or "").strip(),
                    "home_name": str(scene.get("home_name") or "未知家庭").strip(),
                }
            )
        return result

    def _tool_settings(self) -> dict[str, Any]:
        scene_config = self._config.get("scene_tool", {})
        if not isinstance(scene_config, dict):
            scene_config = {}
        scene_enable = bool(
            scene_config.get(
                "enable",
                self._config.get("enable_scene_tool", False),
            )
        )
        scene_admin_only = bool(
            scene_config.get(
                "admin_only",
                self._config.get("scene_tool_admin_only", True),
            )
        )
        control_config = self._config.get("control_tool", {})
        if not isinstance(control_config, dict):
            control_config = {}
        allowed_devices = control_config.get("allowed_devices", [])
        if isinstance(allowed_devices, str):
            try:
                allowed_devices = json.loads(allowed_devices)
            except json.JSONDecodeError:
                allowed_devices = []
        if not isinstance(allowed_devices, list):
            allowed_devices = []
        allowed_aliases = []
        for value in allowed_devices:
            alias = str(value or "").strip()
            if alias and alias not in allowed_aliases:
                allowed_aliases.append(alias)

        scene_risky = scene_enable and not scene_admin_only
        control_enable = bool(control_config.get("enable", False))
        control_admin_only = bool(control_config.get("admin_only", True))
        control_risky = control_enable and not control_admin_only
        warnings = []
        if scene_risky:
            warnings.append("场景 Tool 当前允许非管理员调用，可能触发真实自动化。")
        if control_risky:
            warnings.append("设备控制 Tool 当前允许非管理员调用，可能触发真实物理操作。")
        if control_enable and not allowed_aliases:
            warnings.append("设备控制 Tool 已开启但白名单为空，当前控制范围为空。")
        return {
            "scene_tool": {
                "enable": scene_enable,
                "admin_only": scene_admin_only,
            },
            "control_tool": {
                "enable": control_enable,
                "admin_only": control_admin_only,
                "allowed_devices": allowed_aliases,
            },
            "enable_readonly_tool": bool(
                self._config.get("enable_readonly_tool", False)
            ),
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 概览与登录
    # ------------------------------------------------------------------

    def _reconcile_login_result(self, login: dict[str, Any]) -> None:
        state_recovered = bool(login.get("login_state_recovered")) or (
            bool(login.get("auth_exists"))
            and not login.get("credential_storage_error")
            and not login.get("last_login_error")
        )
        if not state_recovered:
            return
        if self._login_result.get("status") != "error":
            return
        detail = str(self._login_result.get("detail") or "")
        if "插件状态记录保存失败" not in detail:
            return
        self._login_result = {
            "status": "success",
            "message": "授权成功。",
        }

    async def get_status(self):
        state = self._data_manager.load_state()
        login = await self._client.get_login_status(state)
        self._reconcile_login_result(login)
        credential_present = bool(login.get("auth_exists"))
        login_error = _effective_login_error(login)
        login_error_scope = _classify_login_error_scope(
            login_error,
            credential_present=credential_present,
        )
        rows = self._cached_device_rows(state)
        scenes = self._sanitized_scenes(state)
        device_map, _category_map, mapping_errors = self._mapping_snapshot()
        tool_settings = self._tool_settings()
        unmapped = sum(
            1
            for row in rows
            if not row.get("configured") and not row.get("missing_from_cloud")
        )
        return json_response(
            {
                "ok": True,
                "auth": {
                    "credential_present": credential_present,
                    "credential_state": _credential_state(
                        credential_present,
                        login_error_scope,
                    ),
                    "logged_in": credential_present,
                    "login_in_progress": bool(
                        login.get("login_in_progress")
                        or (
                            self._login_task is not None and not self._login_task.done()
                        )
                    ),
                    "last_login_at": str(login.get("last_login_at") or ""),
                    "last_login_error": login_error,
                    "last_error_scope": login_error_scope,
                    "authorization_problem": (
                        login_error_scope == "authorization"
                    ),
                },
                "summary": {
                    "cloud_device_count": sum(
                        1 for row in rows if not row.get("missing_from_cloud")
                    ),
                    "configured_alias_count": len(device_map),
                    "unmapped_device_count": unmapped,
                    "scene_count": len(scenes),
                    "scene_cache_updated_at": str(
                        login.get("scene_cache_updated_at") or ""
                    ),
                },
                "tools": tool_settings,
                "mapping_errors": mapping_errors,
                "security": {
                    "dashboard_authenticated_api": True,
                    "device_control_available": False,
                    "scene_execution_available": False,
                    "credentials_exposed": False,
                    "llm_device_control_enabled": tool_settings["control_tool"][
                        "enable"
                    ],
                },
            }
        )

    async def start_login(self):
        # 接受空对象，避免跨站表单直接触发；Plugin Page bridge 会发送 JSON。
        await self._read_json_body()
        async with self._login_start_lock:
            if self._login_task is not None and not self._login_task.done():
                raise WebAPIError("扫码登录流程已在进行中。", 409)
            self._ensure_operation_available(allow_login_task=True)
            async with self._operation_lock:
                client_status = await self._client.get_login_status()
                if client_status.get("auth_exists"):
                    self._login_result = {
                        "status": "already_logged_in",
                        "message": "当前已有米家登录凭证。",
                    }
                    return _sensitive_json_response(
                        {"ok": True, **self._login_status_payload()}
                    )

                self._login_qr_url = ""
                self._login_qr_image = ""
                self._login_qr_created_at = 0.0
                self._login_qr_revision = ""
                self._login_started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._login_result = {
                    "status": "starting",
                    "message": "正在启动安全登录进程…",
                }
                self._login_task = asyncio.create_task(
                    self._run_login(),
                    name="mihome-webui-login",
                )
                return _sensitive_json_response(
                    {"ok": True, **self._login_status_payload()},
                    status_code=202,
                )

    async def _run_login(self) -> None:
        async def on_qr(url: str) -> None:
            # URL 只保存在内存中，不写日志和状态文件。
            self._login_qr_url = str(url or "").strip()
            self._login_qr_image = await asyncio.to_thread(
                _build_qr_data_uri,
                self._login_qr_url,
            )
            self._login_qr_created_at = time.monotonic()
            self._login_qr_generation += 1
            self._login_qr_revision = str(self._login_qr_generation)
            self._login_result = {
                "status": "waiting_scan",
                "message": (
                    "推荐使用小米账号“扫一扫”；米家等小米应用或"
                    "微信、微博、QQ 也可扫码。"
                ),
            }

        try:
            result = await self._client.login(qr_callback=on_qr)
            status = str(result.get("status") or "error")
            messages = {
                "success": "授权成功。",
                "already_logged_in": "当前已有米家登录凭证。",
                "timeout": "扫码授权已超时，请重新开始。",
                "qrcode_not_found": "未能生成登录二维码，请查看日志。",
                "in_progress": "扫码登录流程已在进行中。",
                "error": "登录失败，请查看诊断信息或 AstrBot 日志。",
            }
            self._login_result = {
                "status": status,
                "message": messages.get(status, "登录流程已结束。"),
            }
            if status == "error" and result.get("message"):
                self._login_result["detail"] = _redact_text(result.get("message"))
        except asyncio.CancelledError:
            self._login_result = {
                "status": "cancelled",
                "message": "登录流程已取消。",
            }
            raise
        except Exception as exc:
            logger.error(
                "[MiHome WebUI] 登录任务失败: %s",
                type(exc).__name__,
            )
            self._login_result = {
                "status": "error",
                "message": "登录失败，请查看 AstrBot 日志。",
            }
        finally:
            # 已结束的二维码票据不再返回给浏览器。
            self._login_qr_url = ""
            self._login_qr_image = ""
            self._login_qr_created_at = 0.0
            self._login_qr_revision = ""

    def _login_status_payload(
        self,
        known_qr_revision: str = "",
    ) -> dict[str, Any]:
        running = self._login_task is not None and not self._login_task.done()
        qr_image = ""
        qr_revision = ""
        qr_available = False
        # 原始登录 URL 仅在服务端内存中用于生成二维码，不返回给浏览器。
        if (
            running
            and self._login_qr_url
            and time.monotonic() - self._login_qr_created_at <= 130
        ):
            qr_available = bool(self._login_qr_image)
            qr_revision = self._login_qr_revision
            if qr_available and known_qr_revision != qr_revision:
                qr_image = self._login_qr_image
        return {
            "running": running,
            "started_at": self._login_started_at,
            "qr_image": qr_image,
            "qr_available": qr_available,
            "qr_revision": qr_revision,
            "status": self._login_result.get("status", "idle"),
            "message": self._login_result.get("message", ""),
            "detail": _redact_text(self._login_result.get("detail")),
        }

    async def get_auth_status(self):
        login = await self._client.get_login_status()
        self._reconcile_login_result(login)
        credential_present = bool(login.get("auth_exists"))
        known_qr_revision = str(
            request.query.get("qr_revision") or ""
        ).strip()[:64]
        login_payload = self._login_status_payload(known_qr_revision)
        login_error = _effective_login_error(login)
        login_error_scope = _classify_login_error_scope(
            login_error,
            credential_present=credential_present,
        )
        return _sensitive_json_response(
            {
                "ok": True,
                **login_payload,
                "credential_present": credential_present,
                "credential_state": _credential_state(
                    credential_present,
                    login_error_scope,
                ),
                "logged_in": credential_present,
                "last_login_at": str(login.get("last_login_at") or ""),
                "last_login_error": login_error,
                "last_error_scope": login_error_scope,
                "authorization_problem": login_error_scope == "authorization",
            }
        )

    async def logout(self):
        data = await self._read_json_body()
        if data.get("confirm") != "退出登录":
            raise WebAPIError('请发送 confirm="退出登录" 以确认清除凭证。')
        self._ensure_operation_available(allow_login_task=True)
        removed = await self.logout_account()
        return json_response(
            {
                "ok": True,
                "credential_removed": bool(removed),
                "message": "已退出米家账号并重置登录状态。",
            }
        )

    # ------------------------------------------------------------------
    # 设备与映射
    # ------------------------------------------------------------------

    async def get_devices(self):
        rows = self._cached_device_rows()
        device_map, category_map, mapping_errors = self._mapping_snapshot()
        mappings = [
            {
                "alias": alias,
                "did": did,
                "category": (
                    category_map.get(alias, CATEGORY_NONE)
                    if category_map.get(alias, CATEGORY_NONE) in VALID_CATEGORIES
                    else CATEGORY_NONE
                ),
            }
            for alias, did in sorted(device_map.items())
        ]
        return json_response(
            {
                "ok": True,
                "devices": rows,
                "mappings": mappings,
                "categories": list(CATEGORY_OPTIONS),
                "snapshot_at": self._device_snapshot_at,
                "mapping_errors": mapping_errors,
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
            }
        )

    async def sync_devices(self):
        await self._read_json_body()
        self._ensure_operation_available()
        async with self._operation_lock:
            devices = await self._client.get_devices()
            self._device_snapshot = self._compact_device_snapshot(devices)
            self._device_snapshot_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = self._build_device_rows(self._device_snapshot)
        return json_response(
            {
                "ok": True,
                "devices": rows,
                "synced_at": self._device_snapshot_at,
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                "message": f"已同步 {len(rows)} 台设备。",
            }
        )

    def _validate_alias(self, value: Any) -> str:
        if not isinstance(value, str):
            raise WebAPIError("设备别名必须是字符串。")
        alias = value.strip()
        if not alias:
            raise WebAPIError("设备别名不能为空。")
        if alias != value:
            raise WebAPIError(f"设备别名“{alias}”首尾不能包含空格。")
        if len(alias) > MAX_ALIAS_LENGTH:
            raise WebAPIError(
                f"设备别名“{alias[:20]}…”超过 {MAX_ALIAS_LENGTH} 个字符。"
            )
        if _CONTROL_CHAR_PATTERN.search(alias):
            raise WebAPIError(f"设备别名“{alias}”包含不可见控制字符。")
        return alias

    def _validate_did(self, value: Any, alias: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise WebAPIError(f"“{alias}”的 DID 必须是字符串或整数。")
        did = str(value).strip()
        if not did or len(did) > MAX_DID_LENGTH:
            raise WebAPIError(f"“{alias}”的 DID 长度不合法。")
        if not _DID_PATTERN.fullmatch(did):
            raise WebAPIError(f"“{alias}”的 DID 格式不合法。")
        return did

    def _validate_category(self, value: Any, alias: str) -> str:
        if value in (None, ""):
            return CATEGORY_NONE
        if not isinstance(value, str):
            raise WebAPIError(f"“{alias}”的设备类别必须是字符串。")
        category = value.strip()
        if category not in VALID_CATEGORIES:
            raise WebAPIError(f"“{alias}”使用了不支持的设备类别。")
        return category

    def _parse_mapping_rows(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, str]]:
        rows = None
        for key in ("mappings", "rows", "devices"):
            if key in data:
                rows = data[key]
                break

        device_map: dict[str, str] = {}
        category_map: dict[str, str] = {}

        if rows is not None:
            if not isinstance(rows, list):
                raise WebAPIError("mappings 必须是数组。")
            if len(rows) > MAX_MAPPING_COUNT:
                raise WebAPIError(f"单次最多保存 {MAX_MAPPING_COUNT} 条设备映射。")
            for index, item in enumerate(rows, 1):
                if not isinstance(item, dict):
                    raise WebAPIError(f"第 {index} 条映射必须是对象。")
                raw_alias = item.get("alias", "")
                raw_did = item.get("did", "")
                raw_category = item.get("category", CATEGORY_NONE)

                # 表格可包含尚未配置别名的云端设备行；空别名表示不保存。
                if isinstance(raw_alias, str) and not raw_alias.strip():
                    if raw_category not in (None, "", CATEGORY_NONE):
                        raise WebAPIError(f"第 {index} 行设置了类别但没有填写别名。")
                    continue

                alias = self._validate_alias(raw_alias)
                if alias in device_map:
                    raise WebAPIError(f"设备别名“{alias}”重复。")
                did = self._validate_did(raw_did, alias)
                category = self._validate_category(raw_category, alias)
                device_map[alias] = did
                category_map[alias] = category
        else:
            raw_device_map = data.get("device_map")
            raw_category_map = data.get("device_category_map", {})
            if isinstance(raw_device_map, str):
                try:
                    raw_device_map = json.loads(raw_device_map)
                except json.JSONDecodeError as exc:
                    raise WebAPIError("device_map 不是合法 JSON。") from exc
            if isinstance(raw_category_map, str):
                try:
                    raw_category_map = json.loads(raw_category_map)
                except json.JSONDecodeError as exc:
                    raise WebAPIError("device_category_map 不是合法 JSON。") from exc
            if not isinstance(raw_device_map, dict):
                raise WebAPIError("请提供 mappings 数组或 device_map 对象。")
            if not isinstance(raw_category_map, dict):
                raise WebAPIError("device_category_map 必须是对象。")
            if len(raw_device_map) > MAX_MAPPING_COUNT:
                raise WebAPIError(f"单次最多保存 {MAX_MAPPING_COUNT} 条设备映射。")

            extra_categories = set(map(str, raw_category_map)) - set(
                map(str, raw_device_map)
            )
            if extra_categories:
                first = sorted(extra_categories)[0]
                raise WebAPIError(f"类别映射“{first}”没有对应的设备别名。")
            for raw_alias, raw_did in raw_device_map.items():
                alias = self._validate_alias(raw_alias)
                if alias in device_map:
                    raise WebAPIError(f"设备别名“{alias}”重复。")
                did = self._validate_did(raw_did, alias)
                category = self._validate_category(
                    raw_category_map.get(raw_alias, CATEGORY_NONE),
                    alias,
                )
                device_map[alias] = did
                category_map[alias] = category

        return device_map, category_map

    def _mapping_change_summary(
        self,
        old_devices: dict[str, str],
        old_categories: dict[str, str],
        new_devices: dict[str, str],
        new_categories: dict[str, str],
    ) -> dict[str, Any]:
        old_aliases = set(old_devices)
        new_aliases = set(new_devices)
        added = [
            {
                "alias": alias,
                "did": new_devices[alias],
                "category": new_categories.get(alias, CATEGORY_NONE),
            }
            for alias in sorted(new_aliases - old_aliases)
        ]
        removed = [
            {
                "alias": alias,
                "did": old_devices[alias],
                "category": old_categories.get(alias, CATEGORY_NONE),
            }
            for alias in sorted(old_aliases - new_aliases)
        ]
        changed = []
        for alias in sorted(old_aliases & new_aliases):
            before = {
                "did": old_devices[alias],
                "category": old_categories.get(alias, CATEGORY_NONE),
            }
            after = {
                "did": new_devices[alias],
                "category": new_categories.get(alias, CATEGORY_NONE),
            }
            if before != after:
                changed.append(
                    {
                        "alias": alias,
                        "before": before,
                        "after": after,
                    }
                )
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "counts": {
                "added": len(added),
                "removed": len(removed),
                "changed": len(changed),
                "total": len(new_devices),
            },
        }

    async def save_device_mappings(self):
        data = await self._read_json_body()
        expected_revision = self._expected_revision(data)
        self._ensure_operation_available()
        self._ensure_revision(expected_revision, _MAPPING_REVISION_KEYS)
        new_device_map, new_category_map = self._parse_mapping_rows(data)
        old_device_map, old_category_map, _ = self._mapping_snapshot()

        known_devices = {
            row["did"]
            for row in self._cached_device_rows()
            if not row.get("missing_from_cloud")
        }
        # 已有的离线/失联映射仍可原样保留，避免用户为了修改其他设备
        # 而被迫删除旧配置；仅拦截新加入且未在最近同步结果中出现的 DID。
        unknown_dids = sorted(
            set(new_device_map.values()) - known_devices - set(old_device_map.values())
        )
        if known_devices and unknown_dids:
            raise WebAPIError(
                "以下 DID 不在最近同步的设备清单中："
                + "、".join(unknown_dids[:8])
                + ("…" if len(unknown_dids) > 8 else "")
            )

        # 旧版本允许存在尚未绑定 DID 的类别项。管理页没有足够信息安全
        # 地判断它们是否应删除，因此保存时继续保留，避免静默丢配置。
        preserved_orphan_categories = {
            alias: category
            for alias, category in old_category_map.items()
            if alias not in old_device_map and alias not in new_category_map
        }
        persisted_category_map = {
            **preserved_orphan_categories,
            **new_category_map,
        }
        summary = self._mapping_change_summary(
            old_device_map,
            old_category_map,
            new_device_map,
            persisted_category_map,
        )
        summary["preserved_orphan_categories"] = sorted(preserved_orphan_categories)
        current_control = self._tool_settings()["control_tool"]
        allowed_aliases = set(current_control["allowed_devices"])
        removed_aliases = set(old_device_map) - set(new_device_map)
        rebound_aliases = {
            alias
            for alias in set(old_device_map) & set(new_device_map)
            if old_device_map[alias] != new_device_map[alias]
        }
        control_allowlist_removed = sorted(
            allowed_aliases & (removed_aliases | rebound_aliases)
        )
        summary["control_allowlist_removed"] = control_allowlist_removed
        if data.get("confirm") is not True:
            return json_response(
                {
                    "ok": True,
                    "saved": False,
                    "requires_confirmation": True,
                    "changes": summary,
                    "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                }
            )

        async with self._operation_lock:
            self._ensure_revision(expected_revision, _MAPPING_REVISION_KEYS)
            old_device_raw = self._config.get("device_map", "{}")
            old_category_raw = self._config.get("device_category_map", "{}")
            had_control_config = "control_tool" in self._config
            old_control_config = deepcopy(
                self._config.get("control_tool", {})
            )
            self._config["device_map"] = json.dumps(
                new_device_map,
                ensure_ascii=False,
                indent=2,
            )
            self._config["device_category_map"] = json.dumps(
                persisted_category_map,
                ensure_ascii=False,
                indent=2,
            )
            if control_allowlist_removed:
                self._config["control_tool"] = {
                    **current_control,
                    "allowed_devices": [
                        alias
                        for alias in current_control["allowed_devices"]
                        if alias not in control_allowlist_removed
                    ],
                }
            try:
                await self._persist_config()
            except Exception:
                self._config["device_map"] = old_device_raw
                self._config["device_category_map"] = old_category_raw
                if had_control_config:
                    self._config["control_tool"] = old_control_config
                else:
                    self._config.pop("control_tool", None)
                raise

        return json_response(
            {
                "ok": True,
                "saved": True,
                "changes": summary,
                "control_tool": self._tool_settings()["control_tool"],
                "revision": self._config_revision(_MAPPING_REVISION_KEYS),
                "tool_revision": self._config_revision(_TOOL_REVISION_KEYS),
                "message": "设备别名与类别映射已保存。",
            }
        )

    async def get_readonly_device_status(self):
        alias = str(request.query.get("alias") or "")
        status = await self.extension_read_device_status(alias)
        properties = status["properties"]
        text = "、".join(f"{key}: {value}" for key, value in properties.items())
        return json_response(
            {
                "ok": True,
                "alias": alias,
                "text": text,
                "status": status,
                "read_only": True,
            }
        )

    # ------------------------------------------------------------------
    # 场景（仅同步与展示，不提供执行）
    # ------------------------------------------------------------------

    async def get_scenes(self):
        state = self._data_manager.load_state()
        return json_response(
            {
                "ok": True,
                "scenes": self._sanitized_scenes(state),
                "cache_updated_at": str(state.get("scene_cache_updated_at") or ""),
                "execution_available": False,
            }
        )

    async def sync_scenes(self):
        await self._read_json_body()
        self._ensure_operation_available()
        async with self._operation_lock:
            scenes = await self._client.get_scenes()
        state = self._data_manager.load_state()
        return json_response(
            {
                "ok": True,
                "scenes": self._sanitized_scenes(state),
                "count": len(scenes),
                "cache_updated_at": str(
                    state.get("scene_cache_updated_at") or ""
                ),
                "execution_available": False,
                "message": f"已同步 {len(scenes)} 个场景。",
            }
        )

    # ------------------------------------------------------------------
    # Tool 设置
    # ------------------------------------------------------------------

    async def get_tool_settings(self):
        return json_response(
            {
                "ok": True,
                **self._tool_settings(),
                "revision": self._config_revision(_TOOL_REVISION_KEYS),
            }
        )

    async def save_tool_settings(self):
        data = await self._read_json_body()
        expected_revision = self._expected_revision(data)
        supported_fields = {
            "enable_readonly_tool",
            "scene_tool",
            "control_tool",
            "confirm_public_scene_tool",
            "confirm_control_tool",
            "confirm_public_control_tool",
            "revision",
        }
        if set(data) - supported_fields:
            raise WebAPIError("Tool 设置包含不支持的字段。")
        scene = data.get("scene_tool")
        control = data.get("control_tool")
        if not isinstance(scene, dict):
            raise WebAPIError("scene_tool 必须是对象。")
        if set(scene) - {"enable", "admin_only"}:
            raise WebAPIError("scene_tool 包含不支持的配置项。")
        if not isinstance(scene.get("enable"), bool):
            raise WebAPIError("scene_tool.enable 必须是布尔值。")
        if not isinstance(scene.get("admin_only"), bool):
            raise WebAPIError("scene_tool.admin_only 必须是布尔值。")
        if not isinstance(data.get("enable_readonly_tool"), bool):
            raise WebAPIError("enable_readonly_tool 必须是布尔值。")
        if not isinstance(control, dict):
            raise WebAPIError("control_tool 必须是对象。")
        if set(control) - {"enable", "admin_only", "allowed_devices"}:
            raise WebAPIError("control_tool 包含不支持的配置项。")
        if not isinstance(control.get("enable"), bool):
            raise WebAPIError("control_tool.enable 必须是布尔值。")
        if not isinstance(control.get("admin_only"), bool):
            raise WebAPIError("control_tool.admin_only 必须是布尔值。")
        allowed_devices = control.get("allowed_devices")
        if not isinstance(allowed_devices, list):
            raise WebAPIError("control_tool.allowed_devices 必须是数组。")
        if len(allowed_devices) > MAX_MAPPING_COUNT:
            raise WebAPIError(
                f"control_tool.allowed_devices 最多允许 {MAX_MAPPING_COUNT} 项。"
            )

        device_map, _category_map, mapping_errors = self._mapping_snapshot()
        if mapping_errors:
            raise WebAPIError("设备映射格式无效，请先修复设备映射。")
        normalized_allowed = []
        for index, value in enumerate(allowed_devices, 1):
            if not isinstance(value, str):
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单第 {index} 项必须是字符串。")
            alias = value.strip()
            if (
                not alias
                or len(alias) > MAX_ALIAS_LENGTH
                or _CONTROL_CHAR_PATTERN.search(alias)
            ):
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单第 {index} 项不是有效别名。")
            if alias not in device_map:
                if not control["enable"]:
                    continue
                raise WebAPIError(f"设备白名单别名“{alias}”不在 device_map 中。")
            if alias not in normalized_allowed:
                normalized_allowed.append(alias)

        if (
            scene["enable"]
            and not scene["admin_only"]
            and data.get("confirm_public_scene_tool") is not True
        ):
            raise WebAPIError(
                "允许非管理员调用场景 Tool 可能触发真实自动化；"
                "确认风险后请提交 confirm_public_scene_tool=true。"
            )
        if control["enable"] and data.get("confirm_control_tool") is not True:
            raise WebAPIError(
                "设备控制 Tool 可以触发真实物理操作；"
                "确认风险后请提交 confirm_control_tool=true。"
            )
        if (
            control["enable"]
            and not control["admin_only"]
            and data.get("confirm_public_control_tool") is not True
        ):
            raise WebAPIError(
                "允许非管理员调用设备控制 Tool 风险很高；"
                "再次确认后请提交 confirm_public_control_tool=true。"
            )

        old_readonly = self._config.get("enable_readonly_tool", False)
        managed_keys = (
            "scene_tool",
            "control_tool",
            "enable_readonly_tool",
            "enable_scene_tool",
            "scene_tool_admin_only",
        )
        original_values = {
            key: deepcopy(self._config[key])
            for key in managed_keys
            if key in self._config
        }
        new_scene = {
            "enable": scene["enable"],
            "admin_only": scene["admin_only"],
        }
        new_control = {
            "enable": control["enable"],
            "admin_only": control["admin_only"],
            "allowed_devices": normalized_allowed,
        }
        changes = {
            "scene_tool": {
                "before": self._tool_settings()["scene_tool"],
                "after": new_scene,
            },
            "enable_readonly_tool": {
                "before": bool(old_readonly),
                "after": data["enable_readonly_tool"],
            },
            "control_tool": {
                "before": self._tool_settings()["control_tool"],
                "after": new_control,
            },
        }

        self._ensure_operation_available()
        async with self._operation_lock:
            self._ensure_revision(expected_revision, _TOOL_REVISION_KEYS)
            self._config["scene_tool"] = new_scene
            self._config["control_tool"] = new_control
            self._config["enable_readonly_tool"] = data["enable_readonly_tool"]
            # 与旧版隐藏字段保持一致，避免降级后出现权限反转。
            self._config["enable_scene_tool"] = new_scene["enable"]
            self._config["scene_tool_admin_only"] = new_scene["admin_only"]
            try:
                await self._persist_config()
            except Exception:
                for key in managed_keys:
                    if key in original_values:
                        self._config[key] = original_values[key]
                    else:
                        self._config.pop(key, None)
                raise

        return json_response(
            {
                "ok": True,
                "saved": True,
                "changes": changes,
                **self._tool_settings(),
                "revision": self._config_revision(_TOOL_REVISION_KEYS),
                "mapping_revision": self._config_revision(_MAPPING_REVISION_KEYS),
            }
        )

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------

    def _diagnostic_checks(self, login: dict[str, Any]) -> list[dict[str, str]]:
        device_map, category_map, mapping_errors = self._mapping_snapshot()
        rows = self._cached_device_rows()
        scenes = self._sanitized_scenes()
        checks: list[dict[str, str]] = []

        def add(code: str, level: str, title: str, message: str) -> None:
            checks.append(
                {
                    "code": code,
                    "level": level,
                    "title": title,
                    "message": message,
                }
            )

        credential_present = bool(login.get("auth_exists"))
        login_error = _effective_login_error(login)
        login_error_scope = _classify_login_error_scope(
            login_error,
            credential_present=credential_present,
        )

        if login_error_scope == "authorization":
            add(
                "auth",
                "error",
                "账号授权",
                (
                    "米家云端已拒绝当前登录凭证，请重新授权。"
                    if credential_present
                    else "米家账号授权未完成，请重新扫码登录。"
                ),
            )
        elif login_error_scope == "credential_storage":
            add(
                "auth",
                "error",
                "凭证存储",
                "登录凭证或插件数据目录需要检查。",
            )
        elif credential_present:
            add("auth", "success", "登录凭证", "已检测到米家登录凭证。")
        else:
            add(
                "auth",
                "warning",
                "登录凭证",
                "尚未登录米家账号，请先完成扫码授权。",
            )

        if login_error_scope == "cloud_connection":
            add(
                "cloud_connection",
                "warning",
                "云端连接",
                login_error,
            )
        elif login_error_scope == "login_flow":
            add(
                "login_flow",
                "warning",
                "扫码登录",
                login_error,
            )
        elif login_error_scope == "unknown":
            add(
                "account_status",
                "warning",
                "账号状态",
                login_error,
            )

        if mapping_errors:
            add(
                "mapping_json",
                "error",
                "映射格式",
                "；".join(mapping_errors),
            )
        else:
            add(
                "mapping_json",
                "success",
                "映射格式",
                f"映射格式正常，共 {len(device_map)} 个别名。",
            )

        unknown_categories = [
            alias
            for alias, category in category_map.items()
            if category not in VALID_CATEGORIES
        ]
        orphan_categories = [alias for alias in category_map if alias not in device_map]
        if unknown_categories or orphan_categories:
            messages = []
            if unknown_categories:
                messages.append(f"{len(unknown_categories)} 个别名使用了未知类别")
            if orphan_categories:
                messages.append(f"{len(orphan_categories)} 个类别没有对应设备")
            add(
                "mapping_category",
                "warning",
                "类别映射",
                "，".join(messages) + "。",
            )
        else:
            add(
                "mapping_category",
                "success",
                "类别映射",
                "设备类别映射一致。",
            )

        missing = [row for row in rows if row.get("missing_from_cloud")]
        if missing:
            add(
                "missing_devices",
                "warning",
                "设备匹配",
                f"{len(missing)} 个已配置 DID 不在最近同步的设备清单中。",
            )
        elif rows:
            add(
                "missing_devices",
                "success",
                "设备匹配",
                "已配置设备均可在最近同步清单中找到。",
            )
        else:
            add(
                "missing_devices",
                "info",
                "设备匹配",
                "暂无设备缓存，请先同步设备。",
            )

        if scenes:
            add(
                "scene_cache",
                "success",
                "场景缓存",
                f"已缓存 {len(scenes)} 个场景。",
            )
        else:
            add(
                "scene_cache",
                "info",
                "场景缓存",
                "暂无场景缓存；如需场景 Tool，请先同步。",
            )

        tool_settings = self._tool_settings()
        if (
            tool_settings["scene_tool"]["enable"]
            and not tool_settings["scene_tool"]["admin_only"]
        ):
            add(
                "scene_tool_permission",
                "warning",
                "场景 Tool 权限",
                "场景 Tool 允许非管理员调用，可能触发真实自动化。",
            )
        else:
            add(
                "scene_tool_permission",
                "success",
                "场景 Tool 权限",
                "场景 Tool 已关闭或仅管理员可调用。",
            )

        control_tool = tool_settings["control_tool"]
        if control_tool["enable"] and not control_tool["admin_only"]:
            add(
                "control_tool_permission",
                "warning",
                "设备控制 Tool 权限",
                "设备控制 Tool 允许非管理员调用，存在真实物理操作风险。",
            )
        elif control_tool["enable"] and not control_tool["allowed_devices"]:
            add(
                "control_tool_allowlist",
                "warning",
                "设备控制 Tool 白名单",
                "设备控制 Tool 已开启但白名单为空，当前会失败关闭。",
            )
        elif control_tool["enable"]:
            add(
                "control_tool_permission",
                "success",
                "设备控制 Tool 权限",
                f"设备控制 Tool 仅管理员可用，白名单含 "
                f"{len(control_tool['allowed_devices'])} 台设备。",
            )
        else:
            add(
                "control_tool_permission",
                "success",
                "设备控制 Tool 权限",
                "设备控制 Tool 已关闭。",
            )

        login_error_title = {
            "authorization": "最近授权异常",
            "credential_storage": "最近凭证存储异常",
            "cloud_connection": "最近云端连接异常",
            "login_flow": "最近扫码登录异常",
        }.get(login_error_scope, "最近账号异常")
        error_fields = (
            ("last_login_error", login_error_title),
            ("last_shared_error", "共享设备异常"),
            ("last_scene_error", "最近场景异常"),
            ("last_control_error", "最近设备控制异常"),
        )
        active_errors = [
            f"{title}：{_redact_text(login.get(key), limit=160)}"
            for key, title in error_fields
            if login.get(key)
        ]
        if active_errors:
            add(
                "recent_errors",
                "warning",
                "近期错误",
                "；".join(active_errors),
            )
        else:
            add(
                "recent_errors",
                "success",
                "近期错误",
                "状态缓存中没有近期错误。",
            )

        return checks

    async def get_diagnostics(self):
        login = await self._client.get_login_status()
        checks = self._diagnostic_checks(login)
        return json_response(
            {
                "ok": True,
                "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "checks": checks,
                "summary": {
                    level: sum(1 for item in checks if item["level"] == level)
                    for level in ("success", "info", "warning", "error")
                },
                "privacy": {
                    "credentials_included": False,
                    "login_url_included": False,
                    "device_control_performed": False,
                    "scene_execution_performed": False,
                },
            }
        )

    async def run_diagnostics(self):
        data = await self._read_json_body()
        unsupported = set(data) - {"include_cached_errors"}
        if unsupported:
            raise WebAPIError("诊断接口包含不支持的参数。")
        # 诊断只检查登录状态和本地缓存，不借机调用设备/场景控制接口。
        return await self.get_diagnostics()
