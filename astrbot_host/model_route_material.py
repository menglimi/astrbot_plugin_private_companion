"""回答「你现在用的什么模型」时的只读材料：四类，只给名称。

这份材料从当前会话的 V2 路由和原生 Image 公共配置读取，不走需要管理权限的
模型管理页面，也不读取或暴露任何密钥。
"""

from __future__ import annotations

import json
from typing import Any

from companion_v2.conversation.media.models import MAX_REFERENCE_IMAGES

KEYWORDS = ("模型", "视觉", "生图", "画图", "语音", "tts", "provider")
INSTRUCTION = (
    "这是当前角色窗口实际生效的模型摘要，只列名称，不含密钥、额度或费用。"
    "被问到「你在用什么模型」时按摘要中的 provider 和 model 回答；摘要里有 model 就不要回答看不到。"
    "摘要只有 provider 而没有 model 时，只说明已公开的 provider，并明确具体型号未提供，"
    "不要把 provider 名当成模型名。"
    "某一项标成 unavailable 时，照实说明那一项读不到、并指到「模型与连接」页，"
    "不要拿别的项顶替，也不要猜模型名。")

VISION_KIND = "conversation.context_image_caption"
VISION_KINDS = ("screen.interpretation", VISION_KIND, "conversation.image_review")


def _label(meta) -> str:
    for name in ("id", "type", "provider"):
        value = getattr(meta, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _model_of(meta) -> str:
    for name in ("model", "model_name"):
        value = getattr(meta, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _entry(provider_id: str, model: str = "") -> dict[str, Any]:
    return {"provider": provider_id, "model": model} if model else {"provider": provider_id}


def _image_configuration(native_tool_port):
    """Read the public Image settings once, without exposing credentials."""
    if native_tool_port is None:
        return None
    pending = [native_tool_port]
    visited = set()
    api = None
    while pending:
        port = pending.pop(0)
        if id(port) in visited:
            continue
        visited.add(id(port))
        getter = getattr(port, "image_extension_api", None)
        if callable(getter):
            api = getter()
            if api is not None:
                break
        ports = getattr(port, "ports", ())
        if isinstance(ports, (tuple, list)):
            pending.extend(ports)
    output = api.output_configuration() if api is not None else None
    settings = output.get("settings") if isinstance(output, dict) else None
    return output if isinstance(settings, dict) and settings.get("enabled") is True else None


def _image_routes(settings, operation):
    endpoints = {item.get("id"): item for item in settings.get("endpoints", ())
                 if isinstance(item, dict) and isinstance(item.get("id"), str)}
    route_config = settings.get("operation_routes")
    if isinstance(route_config, dict):
        routes = route_config.get(operation, ())
    else:
        routes = ["online:" + identifier for identifier, item in endpoints.items()
                  if item.get("enabled") is True]
        for backend in ("sd_webui", "comfyui"):
            if isinstance(settings.get(backend), dict) and settings[backend].get("base_url"):
                routes.append(backend)
    return endpoints, routes


def _online_edit_reference_limit(endpoint):
    if (endpoint.get("enabled") is not True or not endpoint.get("base_url")):
        return None
    platform = endpoint.get("platform", "auto")
    try:
        from .native_extensions.image_native.output_routing import (
            XAI_MAX_REFERENCE_IMAGES, effective_platform)
        platform = effective_platform(endpoint)
    except Exception:
        if platform == "auto":
            return None
    if platform not in {"openai", "openrouter", "agnes", "sensenova", "doubao", "xai"}:
        return None
    value = endpoint.get("max_reference_images", MAX_REFERENCE_IMAGES)
    if type(value) is not int or value <= 0:
        return None
    # Grok Imagine edits accept five references at most; the routing layer
    # clamps the saved value the same way.
    if platform == "xai":
        value = min(value, XAI_MAX_REFERENCE_IMAGES)
    return value


def _local_edit_reference_limit(settings, route):
    config = settings.get(route)
    if not isinstance(config, dict) or not config.get("base_url"):
        return None
    if route == "sd_webui":
        return 1
    workflows = config.get("workflows")
    if isinstance(workflows, str):
        try:
            workflows = json.loads(workflows)
        except (TypeError, ValueError):
            return None
    edit = workflows.get("edit") if isinstance(workflows, dict) else None
    mapping = edit.get("mapping") if isinstance(edit, dict) else None
    fields = mapping.get("fields") if isinstance(mapping, dict) else None
    if not isinstance(fields, dict):
        return None
    count = sum(1 for field in fields.values()
                if isinstance(field, dict) and field.get("kind") == "image")
    return count if count > 0 else None


def image_edit_reference_limit(native_tool_port):
    """Return the conservative reference count accepted by the active edit route."""
    try:
        configuration = _image_configuration(native_tool_port)
        if configuration is None:
            return None
        settings = configuration["settings"]
        nai = configuration.get("nai") or {}
        endpoints, routes = _image_routes(settings, "edit")
        limits = []
        for route in routes:
            if not isinstance(route, str):
                continue
            if route.startswith("online:"):
                limit = _online_edit_reference_limit(endpoints.get(route[7:], {}))
            elif route in ("sd_webui", "comfyui"):
                limit = _local_edit_reference_limit(settings, route)
            elif route == "nai" and nai.get("available") is True and "edit" in (nai.get("operations") or ()):
                limit = 1
            else:
                limit = None
            if limit is not None:
                limits.append(limit)
        return min(limits) if limits else None
    except Exception:
        return None


def _host_provider(host_context, kind: str, umo):
    """读宿主当前选中的对话 / TTS Provider；构造和方法缺失都当作没有。"""
    if host_context is None:
        return None, None
    getter = getattr(host_context, kind, None)
    if not callable(getter):
        return None, None
    provider = getter(umo=umo)
    meta = provider.meta() if provider is not None and callable(getattr(provider, "meta", None)) else None
    return provider, meta


def _now(application):
    clock = getattr(application, "now", None)
    try:
        return clock() if callable(clock) else None
    except Exception:
        return None


def _configuration_target(context):
    """Build the same window target used by the model router."""
    from companion_v2.configuration.models import ConfigLayer, ConfigTarget

    scope = getattr(context, "scope", None)
    role_binding = getattr(context, "role_binding", None)
    role = getattr(role_binding, "role", None)
    installation_id = getattr(scope, "installation_id", None)
    if scope is None or role is None or not installation_id:
        return None
    try:
        return ConfigTarget(installation_id, ConfigLayer.WINDOW, role, scope,
                            bot_id=getattr(scope, "bot_id", None))
    except (TypeError, ValueError):
        return None


def _unwrap_read(value):
    if value is None:
        return None
    if getattr(value, "is_success", False):
        try:
            return value.unwrap()
        except Exception:
            return None
    if isinstance(value, dict):
        return value
    return None


def _route_snapshot_from_router(application, context):
    """Read task fields through the ordinary configuration-read path.

    ``ModelManagement.status`` is an operator page API and requires management
    permissions.  The reply path only needs the route values already visible to
    the current task, so it reads each model group directly and falls back to the
    router's provider default when a lightweight host fixture cannot read config.
    """
    router = getattr(application, "router", None)
    profiles = getattr(router, "profiles", None)
    if router is None or not profiles:
        return None
    configuration = getattr(application, "configuration", None)
    actor = getattr(context, "actor", None)
    target = _configuration_target(context)
    observed_at = _now(application)
    tasks = []
    for profile in profiles.values() if hasattr(profiles, "values") else profiles:
        snapshot = None
        if (configuration is not None and callable(getattr(configuration, "read", None)
                ) and actor is not None and target is not None and observed_at is not None):
            try:
                snapshot = _unwrap_read(configuration.read(actor, target=target,
                    group_id=profile.group, observed_at=observed_at))
            except Exception:
                snapshot = None
        provider_id = None
        if snapshot is not None:
            try:
                provider_id = router.provider_default(context, profile, snapshot)
                snapshot = router.apply_provider_default(profile, snapshot, provider_id)
            except Exception:
                provider_id = None
            fields = {}
            raw_fields = (snapshot.get("fields", {}) if isinstance(snapshot, dict)
                          else getattr(snapshot, "fields", {}))
            for name, field in raw_fields.items():
                value = (field.get("value") if isinstance(field, dict)
                         else getattr(field, "value", None))
                fields[name.rsplit(".", 1)[-1]] = {"value": value}
            provider_id = fields.get("provider", {}).get("value") or provider_id
        else:
            try:
                provider_id = router.provider_default(context, profile, None)
            except Exception:
                provider_id = None
            provider_id = provider_id or getattr(profile, "default_provider", None)
            fields = {"provider": {"value": provider_id}} if provider_id else {}
        if fields:
            tasks.append({"kind": getattr(profile, "kind", ""), "fields": fields})
    providers = []
    provider_values = getattr(router, "providers", {})
    values = provider_values.values() if hasattr(provider_values, "values") else provider_values
    for provider in values:
        binding = getattr(provider, "binding", None)
        provider_id = getattr(binding, "provider", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            continue
        model = getattr(provider, "model", "")
        providers.append({"id": provider_id.strip(),
                          "model": model if isinstance(model, str) else "",
                          "capabilities": list(getattr(provider, "capabilities", ()) or ())})
    return {"tasks": tasks, "providers": providers}


def route_snapshot(application, context):
    return _route_snapshot_from_router(application, context)


def image_route_snapshot(native_tool_port):
    """Project the public native Image configuration into model-safe names."""
    unavailable = {"state": "unavailable", "reason": "生图扩展未启用或没有可用路由"}
    if native_tool_port is None:
        return unavailable
    try:
        configuration = _image_configuration(native_tool_port)
        if configuration is None:
            return unavailable
        settings = configuration["settings"]
        nai = configuration.get("nai") or {}
        endpoints, routes = _image_routes(settings, "generate")
        entries = []
        for route in routes:
            if not isinstance(route, str):
                continue
            if route.startswith("online:"):
                endpoint = endpoints.get(route[7:])
                if not isinstance(endpoint, dict) or endpoint.get("enabled") is not True:
                    continue
                if not endpoint.get("base_url"):
                    continue
                model = endpoint.get("model", "")
                platform = endpoint.get("platform", "auto")
                if isinstance(model, str) and model.strip():
                    entries.append(_entry(str(platform).strip() or "auto", model.strip()))
            elif route in ("sd_webui", "comfyui"):
                backend = settings.get(route)
                if isinstance(backend, dict) and backend.get("base_url"):
                    model = backend.get("model", "")
                    entries.append(_entry(route, str(model).strip() if isinstance(model, str) else ""))
            elif route == "nai" and nai.get("available") is True and "generate" in (nai.get("operations") or ()):
                model = nai.get("default_model", "")
                entries.append(_entry("NAI 生图插件", model.strip() if isinstance(model, str) else ""))
        if not entries:
            return unavailable
        result = dict(entries[0])
        if len(entries) > 1:
            result["models"] = [item.get("model", "") for item in entries]
            result["providers"] = [item["provider"] for item in entries]
        return result
    except Exception:
        return unavailable


def _route_from_snapshot(snapshot, *kinds):
    if not isinstance(snapshot, dict):
        return None
    tasks = {item.get("kind"): item for item in snapshot.get("tasks") or [] if isinstance(item, dict)}
    providers = {item.get("id"): item for item in snapshot.get("providers") or [] if isinstance(item, dict)}
    for kind in kinds:
        task = tasks.get(kind)
        fields = task.get("fields") if isinstance(task, dict) else None
        provider = fields.get("provider") if isinstance(fields, dict) else None
        provider_id = provider.get("value") if isinstance(provider, dict) else None
        if isinstance(provider_id, str) and provider_id.strip():
            model = providers.get(provider_id, {}).get("model")
            return _entry(provider_id.strip(), model if isinstance(model, str) else "")
    return None


def build_summary(application, host_context, umo, context, at, native_tool_port=None):
    """汇总四类模型；配置读取失败时只退回当前宿主对话 Provider。"""
    summary: dict[str, Any] = {}
    snapshot = route_snapshot(application, context)
    for title, kinds, getter, reason in (
            ("对话", ("conversation.reply",), "get_using_provider", "宿主没有返回当前对话 Provider"),
            ("视觉理解", VISION_KINDS, None, "视觉任务路由未配置"),
            ("语音合成", (), "get_using_tts_provider", "没有启用或没有选中 TTS Provider")):
        entry = _route_from_snapshot(snapshot, *kinds) if kinds else None
        # Accept snapshots produced by older material providers while all new
        # snapshots use the registered task kinds above.
        if entry is None and title == "视觉理解":
            entry = _route_from_snapshot(snapshot, "vision.review")
        if entry is not None:
            summary[title] = entry
            continue
        try:
            _provider, meta = _host_provider(host_context, getter, umo) if getter else (None, None)
            summary[title] = (_entry(_label(meta), _model_of(meta)) if meta is not None
                              else {"state": "unavailable", "reason": reason})
        except Exception as error:
            summary[title] = {"state": "unavailable", "reason": type(error).__name__}
    image_summary = image_route_snapshot(native_tool_port)
    if image_summary.get("state") == "unavailable":
        image_summary = _route_from_snapshot(snapshot, "image.generate") or image_summary
    summary["生图"] = image_summary
    return summary


def scope_umo(context) -> str:
    """V2 的窗口还原成宿主 UMO（``平台:消息类型:会话``），读不到就返回空串。"""
    scope = getattr(context, "scope", None)
    if scope is None:
        return ""
    kind = "GroupMessage" if getattr(scope, "kind", None) is not None and scope.kind.value == "group" else "FriendMessage"
    platform = getattr(scope, "platform", "") or ""
    native = getattr(scope, "native_scope_id", "") or ""
    return f"{platform}:{kind}:{native}" if platform and native else ""


def model_route_keyword_material(application, host_context, native_tool_port=None):
    from companion_v2.conversation.keyword_materials import KeywordMaterialSpec
    from companion_v2.expression.configuration import source_subject_allowed_for_context
    from companion_v2.foundation.refs import ScopeKind

    async def prepare(context, text, at):
        return {"state": "ok",
                "models": build_summary(application, host_context, scope_umo(context) or None,
                                         context, at, native_tool_port=native_tool_port)}

    def available(context):
        if context.scope.kind is not ScopeKind.PRIVATE:
            return False
        try:
            return bool(source_subject_allowed_for_context(application.runtime, context))
        except Exception:
            return False

    return KeywordMaterialSpec(owner="model.route", title="当前模型", instruction=INSTRUCTION,
        keywords=KEYWORDS, prepare=prepare, available=available, ttl_seconds=600)
