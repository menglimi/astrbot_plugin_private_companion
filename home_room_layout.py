# -*- coding: utf-8 -*-
"""Room appearance preferences in AstrBot's durable plugin KV store."""
from __future__ import annotations

import json
import math
from typing import Any


def layout_storage_key(plugin: Any) -> str:
    # The page route has already resolved and activated the requested persona.
    active = getattr(plugin, "_active_persona_scope", lambda: "")()
    primary = getattr(plugin, "_primary_persona_id", lambda: "")()
    return f"home_room_layout_v1:{active or primary or 'primary'}"


def normalize_room_layout(value: Any) -> dict[str, Any]:
    """Keep layout fields only; placement and catalog compatibility stay in 3D."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("房间布局版本无效")
    items = value.get("items")
    if not isinstance(items, dict) or len(items) > 128:
        raise ValueError("房间家具列表无效")
    result: dict[str, Any] = {"version": 1, "items": {}}
    if "houseStyle" in value:
        if not isinstance(value["houseStyle"], str) or len(value["houseStyle"]) > 64:
            raise ValueError("房屋样式无效")
        result["houseStyle"] = value["houseStyle"]
    for name, item in items.items():
        if not isinstance(name, str) or not name or len(name) > 64 or not isinstance(item, dict):
            raise ValueError("家具布局格式无效")
        fields: dict[str, Any] = {}
        for key in ("x", "y", "z", "rotation"):
            if key in item:
                number = item[key]
                if type(number) not in (int, float) or not math.isfinite(number) or abs(number) > 1_000_000:
                    raise ValueError("家具坐标无效")
                fields[key] = number
        for key in ("style", "model", "surface"):
            if key in item:
                if not isinstance(item[key], str) or len(item[key]) > 64:
                    raise ValueError("家具样式格式无效")
                fields[key] = item[key]
        result["items"][name] = fields
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 65536:
        raise ValueError("房间布局数据过大")
    return result


async def load_room_layout(plugin: Any) -> dict[str, Any] | None:
    value = await plugin.get_kv_data(layout_storage_key(plugin), None)
    return normalize_room_layout(value) if value is not None else None


async def save_room_layout(plugin: Any, value: Any) -> dict[str, Any]:
    layout = normalize_room_layout(value)
    # Await the KV transaction; acknowledging an in-memory draft is not a save.
    await plugin.put_kv_data(layout_storage_key(plugin), layout)
    return layout
