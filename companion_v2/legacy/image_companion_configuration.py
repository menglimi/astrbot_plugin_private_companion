"""Convert the separately installed 6.x Image companion's saved routes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .image_configuration_migration import convert_image_configuration


_SOURCE = "astrbot_plugin_image_companion_config.json"
_OPERATIONS = ("generate", "edit", "mask_edit")


@dataclass(frozen=True)
class ImageCompanionConfigurationMigration:
    changes: dict
    sources: dict[str, tuple[str, ...]]
    differences: tuple[dict, ...]


def _connection(raw):
    if not isinstance(raw, dict):
        return {}
    base = raw.get("base_url")
    if not isinstance(base, str) or not base.strip():
        return {}
    result = {"base_url": base.strip(), "timeout_seconds": raw.get("timeout_seconds", 300)}
    if isinstance(raw.get("api_key"), str):
        result["api_key"] = raw["api_key"]
    if isinstance(raw.get("parameters"), dict):
        result["parameters"] = deepcopy(raw["parameters"])
    return result


def _workflow_routes(raw):
    preparation = raw.get("preparation") if isinstance(raw.get("preparation"), dict) else {}
    comfy = raw.get("comfyui") if isinstance(raw.get("comfyui"), dict) else {}
    items = comfy.get("workflows")
    if not isinstance(items, list):
        return {}, {}, (), {}
    workflows = {item["name"]: item for item in items
                 if isinstance(item, dict) and isinstance(item.get("name"), str)
                 and isinstance(item.get("workflow"), dict)
                 and isinstance(item.get("mapping"), dict)}
    routes = preparation.get("routes")
    routes = {item["route_id"]: item for item in routes
              if isinstance(item, dict) and isinstance(item.get("route_id"), str)} if isinstance(routes, list) else {}
    picked = {}
    for operation, key in (("generate", "default_generate_route"), ("edit", "default_edit_route")):
        route = routes.get(preparation.get(key))
        if (route and route.get("backend") == "comfyui"
                and route.get("operation") == operation and route.get("workflow_id") in workflows):
            picked[operation] = route
    masked = next((item for item in routes.values()
                   if item.get("backend") == "comfyui" and item.get("masked_edit") is True
                   and item.get("workflow_id") in workflows), None)
    if masked is not None:
        picked["mask_edit"] = masked
    selected = {operation: {"workflow": deepcopy(workflows[route["workflow_id"]]["workflow"]),
                            "mapping": deepcopy(workflows[route["workflow_id"]]["mapping"])}
                for operation, route in picked.items()}
    parameters = {operation: deepcopy(route["parameters"])
                  for operation, route in picked.items() if isinstance(route.get("parameters"), dict)}
    unused = tuple(name for name in workflows
                   if name not in {item["workflow_id"] for item in picked.values()})
    names = {operation: route["workflow_id"] for operation, route in picked.items()}
    return selected, parameters, unused, names


def convert_image_companion_configuration(raw, *, private_output=None, private_sources=None):
    """Keep the old plugin's selected routes without reading or writing its data."""
    if not isinstance(raw, dict):
        return ImageCompanionConfigurationMigration({}, {}, ())
    changes = deepcopy(private_output) if isinstance(private_output, dict) else {}
    sources = dict(private_sources) if isinstance(private_sources, dict) else {}
    differences = []
    image = raw.get("image") if isinstance(raw.get("image"), dict) else {}
    photo = dict(image)
    for target, old in (("EXTERNAL_IMAGE_API_BASE_URL", "external_image_api_base_url"),
                        ("EXTERNAL_IMAGE_API_KEY", "external_image_api_key"),
                        ("EXTERNAL_IMAGE_API_MODEL", "external_image_api_model"),
                        ("BACKUP_EXTERNAL_IMAGE_API_BASE_URL", "backup_external_image_api_base_url"),
                        ("BACKUP_EXTERNAL_IMAGE_API_KEY", "backup_external_image_api_key"),
                        ("BACKUP_EXTERNAL_IMAGE_API_MODEL", "backup_external_image_api_model")):
        if old in image:
            photo[target] = image[old]
    online = convert_image_configuration({"photo_action_config": photo})
    if online.output_changes.get("endpoints"):
        changes["endpoints"] = online.output_changes["endpoints"]
        sources["endpoints"] = (_SOURCE + ":image.external_image_api_endpoints",)
        differences.extend(online.differences)

    comfy = _connection(raw.get("comfyui"))
    workflows, parameters, unused, selected_workflows = _workflow_routes(raw)
    if comfy:
        comfy["workflows"] = workflows
        if parameters:
            comfy["parameters"] = {**comfy.get("parameters", {}), **parameters}
        changes["comfyui"] = comfy
        sources["comfyui"] = (_SOURCE + ":comfyui", _SOURCE + ":preparation.routes")
    sd = _connection(raw.get("sd_webui"))
    if sd:
        changes["sd_webui"] = sd
        sources["sd_webui"] = (_SOURCE + ":sd_webui",)

    if unused or selected_workflows:
        options = changes.get("legacy_options")
        options = deepcopy(options) if isinstance(options, dict) else {}
        if selected_workflows:
            options["image_companion_selected_workflows"] = selected_workflows
        if unused:
            options["image_companion_extra_workflows"] = list(unused)
        changes["legacy_options"] = options
        sources["legacy_options"] = tuple(sorted(set(sources.get("legacy_options", ())) |
                                                  {_SOURCE + ":comfyui.workflows"}))
        if unused:
            differences.append({"field": "image.output.legacy_options.image_companion_extra_workflows",
                                "kind": "preserved_not_executable",
                                "explain": "旧 Image 插件还有非默认工作流；已记录名称，未替换当前文生图与改图默认路线。"})

    available = {"comfyui": bool(comfy and workflows), "sd_webui": bool(sd)}
    prep = raw.get("preparation") if isinstance(raw.get("preparation"), dict) else {}
    prep_routes = prep.get("routes") if isinstance(prep.get("routes"), list) else []
    by_id = {item.get("route_id"): item for item in prep_routes if isinstance(item, dict)}
    operation_routes = {}
    for operation, default_key in (("generate", "default_generate_route"),
                                   ("edit", "default_edit_route")):
        route = by_id.get(prep.get(default_key))
        selected = route.get("backend") if isinstance(route, dict) else None
        local = selected if (
            selected == "sd_webui" and available["sd_webui"]
            or selected == "comfyui" and available["comfyui"]
            and (operation in workflows or operation == "edit" and "mask_edit" in workflows)
        ) else None
        online_routes = ["online:" + item["id"] for item in changes.get("endpoints", [])]
        operation_routes[operation] = ([local] if local else []) + online_routes
        if not local and selected in available:
            differences.append({"field": "image.output." + operation, "kind": "incomplete_backend",
                                "explain": "旧 Image 插件默认路线缺少可绑定的连接或工作流，已保留原配置但不启用。"})
    if not any(operation_routes.values()):
        if not any(changes.get(field) for field in (
                "endpoints", "comfyui", "sd_webui", "legacy_options", "network")):
            return ImageCompanionConfigurationMigration({}, {}, tuple(differences))
        differences.append({"field": "image.output.operation_routes", "kind": "incomplete_backend",
                            "explain": "旧 Image 插件没有可绑定的默认路线；连接和参数已保留，生图后端保持关闭。"})
    executable_online = any(item.get("enabled") and item.get("base_url") and item.get("model")
        and item.get("platform") in {"auto", "openai", "openrouter", "agnes", "sensenova",
                                     "minimax", "doubao", "xai"}
        for item in changes.get("endpoints", []))
    active = raw.get("enabled") is True and prep.get("chat_enabled", True) is not False
    changes["backend"] = "auto"
    changes["operation_routes"] = operation_routes
    routed_local = any(
        "comfyui" in routes and (operation in workflows
            or operation == "edit" and "mask_edit" in workflows)
        or "sd_webui" in routes and available["sd_webui"]
        for operation, routes in operation_routes.items()
    )
    routed_online = executable_online and any(
        route.startswith("online:") for routes in operation_routes.values() for route in routes
    )
    changes["enabled"] = bool(active and (routed_local or routed_online))
    for field in ("backend", "operation_routes", "enabled"):
        sources[field] = (_SOURCE + ":preparation",)
    differences.append({"field": "image.output", "kind": "image_companion_source",
                        "explain": "生图连接和默认工作流优先采用独立 Image 插件的已保存配置。"})
    return ImageCompanionConfigurationMigration(changes, sources, tuple(differences))
