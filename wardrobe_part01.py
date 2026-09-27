# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

try:
    from .wardrobe_shared import _wardrobe_host
except ImportError:  # 离线工具把本模块当顶层模块加载
    from wardrobe_shared import _wardrobe_host


def clean_wardrobe_text(value: Any, limit: int = 0) -> str:
    """Collapse whitespace and optionally truncate a single-line field."""

    text = _wardrobe_host._WHITESPACE_PATTERN.sub(" ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[:limit].rstrip()
    return text

def clean_wardrobe_multiline(value: Any, limit: int = 0) -> str:
    """Normalize newlines without collapsing them (tendency may be multi-line)."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    if limit > 0 and len(cleaned) > limit:
        return cleaned[:limit].rstrip()
    return cleaned

def normalize_wardrobe_tags(value: Any) -> list[str]:
    """Return a de-duplicated, ordered tag list."""

    if isinstance(value, str):
        raw: Iterable[Any] = _wardrobe_host._TAG_SPLIT_PATTERN.split(value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = ()
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _wardrobe_host.clean_wardrobe_text(item, _wardrobe_host.WARDROBE_MAX_TAG)
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= _wardrobe_host.WARDROBE_MAX_TAGS:
            break
    return result

def normalize_wardrobe_tendency(value: Any) -> str:
    """Normalize the overall clothing-tendency text."""

    return _wardrobe_host.clean_wardrobe_multiline(value, _wardrobe_host.WARDROBE_MAX_TENDENCY)

def normalize_wardrobe_bool(value: Any, default: bool = False) -> bool:
    """Coerce a stored boolean without treating the string "false" as truthy."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "1", "yes", "on", "y", "是", "开", "开启", "启用"}:
            return True
        if lowered in {"false", "0", "no", "off", "n", "否", "关", "关闭", "禁用"}:
            return False
        if not lowered:
            return False
    if value is None:
        return default
    return bool(value)

def normalize_wardrobe_slot(value: Any) -> str:
    """Return one of WARDROBE_SLOTS, or an empty string when unclassified."""

    text = _wardrobe_host.clean_wardrobe_text(value, _wardrobe_host.WARDROBE_MAX_TAG).casefold()
    if not text:
        return ""
    if text in _wardrobe_host.WARDROBE_SLOTS:
        return text
    if text in _wardrobe_host._SLOT_ALIASES:
        return _wardrobe_host._SLOT_ALIASES[text]
    for token, slot in _wardrobe_host._SLOT_SUBSTRING_HINTS:
        if token in text:
            return slot
    return ""

def infer_wardrobe_slot(name: Any, description: Any = "") -> str:
    """Guess a slot from free text, name first.

    Vision models often omit 部位.  A name such as 「米色针织开衫」 still
    classifies from the keyword table; anything unclassifiable stays empty
    rather than guessing, because 「未分类」 is an honest state we surface in
    the panel instead of hiding behind a wrong guess.
    """

    for candidate in (name, description):
        slot = _wardrobe_host.normalize_wardrobe_slot(candidate)
        if slot:
            return slot
    return ""

def normalize_wardrobe_precision(value: Any) -> str:
    """Return PRECISION_EXACT (default) or PRECISION_LOOSE."""

    text = _wardrobe_host.clean_wardrobe_text(value, _wardrobe_host.WARDROBE_MAX_TAG).casefold()
    if text in {"loose", "fuzzy", "loose_fit", "模糊", "大致", "随意", "宽松"}:
        return _wardrobe_host.PRECISION_LOOSE
    return _wardrobe_host.PRECISION_EXACT

def normalize_wardrobe_image_kind(value: Any) -> str:
    """Return one of WARDROBE_IMAGE_KINDS, or an empty string when unknown."""

    text = _wardrobe_host.clean_wardrobe_text(value, 32).casefold()
    if not text:
        return ""
    if text in _wardrobe_host.WARDROBE_IMAGE_KINDS:
        return text
    return _wardrobe_host._IMAGE_KIND_ALIASES.get(text, "")

def normalize_wardrobe_ownership(value: Any) -> str:
    """Return OWNERSHIP_OWNED (default) or OWNERSHIP_REFERENCE."""

    text = _wardrobe_host.clean_wardrobe_text(value, 24).casefold()
    if text in {"reference", "ref", "参考", "灵感", "喜欢", "别人的", "别人"}:
        return _wardrobe_host.OWNERSHIP_REFERENCE
    return _wardrobe_host.OWNERSHIP_OWNED

def normalize_asset_ids(value: Any) -> list[str]:
    """Normalize references to the asset layer (素材层 id)."""

    if isinstance(value, str):
        raw_items: list[Any] = [part for part in re.split(r"[,，、;；\s]+", value) if part]
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        return []
    result: list[str] = []
    for raw in raw_items:
        text = _wardrobe_host.clean_wardrobe_text(raw, 80)
        if text and text not in result:
            result.append(text)
        if len(result) >= _wardrobe_host.WARDROBE_MAX_ASSET_IDS:
            break
    return result

def _first_present(raw: Mapping[str, Any], *keys: str) -> Any:
    """Return the first key that is present and not None.

    A plain "raw.get(a) or raw.get(b)" would let a legitimate False fall through
    to the next alias -- which is exactly how intimate=False would silently become
    intimate=True.
    """

    for key in keys:
        if key in raw and raw.get(key) is not None:
            return raw.get(key)
    return None

def wardrobe_item_name_key(value: Any) -> str:
    """Return the case/space-insensitive identity key for an item name."""

    return _wardrobe_host.clean_wardrobe_text(value, _wardrobe_host.WARDROBE_MAX_NAME).casefold().replace(" ", "")

def find_wardrobe_item_by_exact_name(
    items: Sequence[Mapping[str, Any]] | None, name: Any
) -> dict[str, Any] | None:
    """按**精确同名**查找散件。

    与 :func:`find_wardrobe_item` 的区别：那条是给「用户点名」用的宽松规则
    （id → 1-based 序号 → 名字 → 子串）。草稿落库判断「是不是同一件」必须用精确同名，
    否则一件叫「3」或「开衫」的新衣物会被误判成已存在：replaced 报 true、面板取回
    别人那一行，而列表里其实新建了一条。
    """

    key = _wardrobe_host.wardrobe_item_name_key(name)
    if not key:
        return None
    for row in _wardrobe_host.normalize_wardrobe_items(list(items or ())):
        if _wardrobe_host.wardrobe_item_name_key(row.get("name")) == key:
            return row
    return None

def find_wardrobe_outfit_by_exact_name(
    outfits: Sequence[Mapping[str, Any]] | None, name: Any
) -> dict[str, Any] | None:
    """按精确同名查找整套（理由同 :func:`find_wardrobe_item_by_exact_name`）。"""

    key = _wardrobe_host.wardrobe_item_name_key(name)
    if not key:
        return None
    for row in _wardrobe_host.normalize_wardrobe_outfits(list(outfits or ())):
        if _wardrobe_host.wardrobe_item_name_key(row.get("name")) == key:
            return row
    return None

def _normalize_source_kind(value: Any, *, source: str) -> str:
    text = _wardrobe_host.clean_wardrobe_text(value, 20).casefold()
    aliases = {
        "": _wardrobe_host.SOURCE_KIND_MANUAL,
        "manual": _wardrobe_host.SOURCE_KIND_MANUAL,
        "text": _wardrobe_host.SOURCE_KIND_MANUAL,
        "手工": _wardrobe_host.SOURCE_KIND_MANUAL,
        "手写": _wardrobe_host.SOURCE_KIND_MANUAL,
        "image": _wardrobe_host.SOURCE_KIND_IMAGE,
        "photo": _wardrobe_host.SOURCE_KIND_IMAGE,
        "picture": _wardrobe_host.SOURCE_KIND_IMAGE,
        "图片": _wardrobe_host.SOURCE_KIND_IMAGE,
        "识图": _wardrobe_host.SOURCE_KIND_IMAGE,
    }
    kind = aliases.get(text, "")
    if kind in _wardrobe_host._SOURCE_KINDS:
        return kind
    # Unknown value: fall back to whether an image source is actually present.
    return _wardrobe_host.SOURCE_KIND_IMAGE if source else _wardrobe_host.SOURCE_KIND_MANUAL

def normalize_wardrobe_item(
    raw: Any,
    *,
    now: float | None = None,
    fallback_id: str = "",
) -> dict[str, Any] | None:
    """Normalize one stored wardrobe item; return ``None`` when unusable."""

    if not isinstance(raw, Mapping):
        return None
    name = _wardrobe_host.clean_wardrobe_text(raw.get("name") or raw.get("title"), _wardrobe_host.WARDROBE_MAX_NAME)
    description = _wardrobe_host.clean_wardrobe_text(
        raw.get("description") or raw.get("note") or raw.get("desc"),
        _wardrobe_host.WARDROBE_MAX_DESCRIPTION,
    )
    if not name and not description:
        return None
    if not name:
        # Derive a short stand-in name so entries always render meaningfully.
        name = _wardrobe_host.clean_wardrobe_text(description, 12) or "未命名衣物"
    source = _wardrobe_host.clean_wardrobe_text(raw.get("source") or raw.get("path") or raw.get("url"), _wardrobe_host.WARDROBE_MAX_SOURCE)
    timestamp = float(now if now is not None else time.time())
    created_at = _wardrobe_host._safe_timestamp(raw.get("created_at"), timestamp)
    updated_at = _wardrobe_host._safe_timestamp(raw.get("updated_at"), created_at)
    return {
        "id": _wardrobe_host.clean_wardrobe_text(raw.get("id"), 80)
        or fallback_id
        or _wardrobe_host._derived_item_id(name, description),
        "name": name,
        "description": description,
        # 部位是唯一分类维度；未知值一律落回「未分类」而不是报错。
        "slot": _wardrobe_host.normalize_wardrobe_slot(_wardrobe_host._first_present(raw, "slot", "category", "part")),
        # 贴身私密衣物：只走对话层，不进生图投影。
        "intimate": _wardrobe_host.normalize_wardrobe_bool(_wardrobe_host._first_present(raw, "intimate", "underwear"), False),
        # 约束强度：exact 必须照此，loose 仅作方向提示。
        "precision": _wardrobe_host.normalize_wardrobe_precision(_wardrobe_host._first_present(raw, "precision")),
        "tags": _wardrobe_host.normalize_wardrobe_tags(raw.get("tags")),
        # 素材层引用：这件衣物对应的图片（可能多张）
        "asset_ids": _wardrobe_host.normalize_asset_ids(_wardrobe_host._first_present(raw, "asset_ids", "assets")),
        # 归属：我拥有的 / 我喜欢的（参考）
        "ownership": _wardrobe_host.normalize_wardrobe_ownership(raw.get("ownership")),
        "source": source,
        "source_kind": _wardrobe_host._normalize_source_kind(raw.get("source_kind"), source=source),
        "created_at": created_at,
        "updated_at": updated_at,
        "version": _wardrobe_host.WARDROBE_VERSION,
    }

def _derived_item_id(name: str, description: str) -> str:
    """Stable fallback id derived from the item content.

    A uuid4 here would be regenerated on every normalization pass, and since the
    rule selector orders candidates by id, a hand-written config without ids
    would pick a *different outfit on every call* -- exactly the flapping the
    determinism requirement exists to prevent.
    """

    digest = hashlib.sha256(f"{name}\u0000{description}".encode("utf-8")).hexdigest()
    return f"wardrobe_{digest[:12]}"

def _safe_timestamp(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number <= 0:
        return fallback
    return number

def normalize_wardrobe_items(value: Any) -> list[dict[str, Any]]:
    """Normalize a stored item list, dropping duplicates and empty rows."""

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in value:
        item = _wardrobe_host.normalize_wardrobe_item(raw)
        if item is None:
            continue
        if item["id"] in seen_ids:
            continue
        name_key = _wardrobe_host.wardrobe_item_name_key(item["name"])
        if name_key and name_key in seen_names:
            continue
        seen_ids.add(item["id"])
        if name_key:
            seen_names.add(name_key)
        result.append(item)
        if len(result) >= _wardrobe_host.WARDROBE_MAX_ITEMS:
            break
    return result

def new_wardrobe_item(
    name: Any,
    description: Any = "",
    *,
    tags: Any = None,
    slot: Any = "",
    intimate: Any = False,
    precision: Any = _wardrobe_host.PRECISION_EXACT,
    source: Any = "",
    source_kind: Any = "",
    asset_ids: Any = None,
    ownership: Any = _wardrobe_host.OWNERSHIP_OWNED,
    now: float | None = None,
) -> dict[str, Any]:
    """Build one normalized item payload, raising when it is unusable."""

    timestamp = float(now if now is not None else time.time())
    item = _wardrobe_host.normalize_wardrobe_item(
        {
            "name": name,
            "description": description,
            "tags": tags,
            "slot": slot,
            "intimate": intimate,
            "precision": precision,
            "asset_ids": asset_ids,
            "ownership": ownership,
            "source": source,
            "source_kind": source_kind or (_wardrobe_host.SOURCE_KIND_IMAGE if _wardrobe_host.clean_wardrobe_text(source) else _wardrobe_host.SOURCE_KIND_MANUAL),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    if item is None:
        raise _wardrobe_host.WardrobeError("衣物条目需要名称或描述")
    return item

def add_wardrobe_item(
    items: Sequence[Mapping[str, Any]] | None,
    *,
    name: Any,
    description: Any = "",
    tags: Any = None,
    slot: Any = "",
    intimate: Any = False,
    precision: Any = _wardrobe_host.PRECISION_EXACT,
    source: Any = "",
    source_kind: Any = "",
    asset_ids: Any = None,
    ownership: Any = _wardrobe_host.OWNERSHIP_OWNED,
    replace_existing: bool = True,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append or replace one item; return the new list and the stored item.

    Same-name entries are treated as the same garment: with
    ``replace_existing`` the previous row is updated in place so a re-described
    photo refreshes the entry instead of piling up duplicates.
    """

    existing = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    incoming = _wardrobe_host.new_wardrobe_item(
        name,
        description,
        tags=tags,
        slot=slot,
        intimate=intimate,
        precision=precision,
        source=source,
        source_kind=source_kind,
        asset_ids=asset_ids,
        ownership=ownership,
        now=now,
    )
    name_key = _wardrobe_host.wardrobe_item_name_key(incoming["name"])
    for index, item in enumerate(existing):
        if _wardrobe_host.wardrobe_item_name_key(item["name"]) != name_key:
            continue
        if not replace_existing:
            raise _wardrobe_host.WardrobeError(f"衣柜里已经有「{item['name']}」了")
        merged = dict(item)
        merged["name"] = incoming["name"]
        merged["description"] = incoming["description"] or item.get("description", "")
        merged["tags"] = incoming["tags"] or list(item.get("tags") or [])
        # 新字段沿用同样的「空值保留旧值」语义：重新识图描述一件衣服时，
        # 不该把已经填好的部位/场景/贴身标记清掉。
        merged["slot"] = incoming["slot"] or item.get("slot", "")
        merged["intimate"] = bool(incoming["intimate"]) or _wardrobe_host.normalize_wardrobe_bool(item.get("intimate"))
        # precision 的默认值 exact 是有意义的值而非空值，所以只有显式传 loose
        # 才覆盖；想从 loose 改回 exact 请用 update_wardrobe_item。
        merged["precision"] = (
            _wardrobe_host.PRECISION_LOOSE
            if incoming["precision"] == _wardrobe_host.PRECISION_LOOSE
            else str(item.get("precision") or _wardrobe_host.PRECISION_EXACT)
        )
        # 素材引用取并集：同一件衣物重新识图时应当"多一张图"，而不是覆盖掉旧的
        merged_assets = list(item.get("asset_ids") or [])
        for asset_id in incoming.get("asset_ids") or ():
            if asset_id not in merged_assets:
                merged_assets.append(asset_id)
        merged["asset_ids"] = _wardrobe_host.normalize_asset_ids(merged_assets)
        merged["ownership"] = incoming.get("ownership") or item.get("ownership") or _wardrobe_host.OWNERSHIP_OWNED
        merged["source"] = incoming["source"] or item.get("source", "")
        merged["source_kind"] = incoming["source_kind"]
        merged["created_at"] = item.get("created_at") or incoming["created_at"]
        merged["updated_at"] = incoming["updated_at"]
        existing[index] = merged
        return existing, merged
    if len(existing) >= _wardrobe_host.WARDROBE_MAX_ITEMS:
        raise _wardrobe_host.WardrobeLimitError(f"衣柜最多 {_wardrobe_host.WARDROBE_MAX_ITEMS} 件，请先删除不用的衣物")
    existing.append(incoming)
    return existing, incoming

def find_wardrobe_item(
    items: Sequence[Mapping[str, Any]] | None,
    reference: Any,
) -> dict[str, Any] | None:
    """Look up an item by id, 1-based index, or name substring."""

    normalized = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    text = _wardrobe_host.clean_wardrobe_text(reference, 80)
    if not text or not normalized:
        return None
    for item in normalized:
        if item["id"] == text:
            return item
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(normalized):
            return normalized[index]
    name_key = _wardrobe_host.wardrobe_item_name_key(text)
    for item in normalized:
        if _wardrobe_host.wardrobe_item_name_key(item["name"]) == name_key:
            return item
    for item in normalized:
        if name_key and name_key in _wardrobe_host.wardrobe_item_name_key(item["name"]):
            return item
    return None

def resolve_wardrobe_reference(
    items: Sequence[Mapping[str, Any]] | None,
    reference: Any,
) -> dict[str, Any] | None:
    """Backwards-compatible alias of :func:`find_wardrobe_item`."""

    return _wardrobe_host.find_wardrobe_item(items, reference)

def delete_wardrobe_item(
    items: Sequence[Mapping[str, Any]] | None,
    reference: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove one item; return ``(remaining_items, removed_item)``."""

    normalized = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    target = _wardrobe_host.find_wardrobe_item(normalized, reference)
    if target is None:
        raise KeyError(_wardrobe_host.clean_wardrobe_text(reference, 80))
    remaining = [item for item in normalized if item["id"] != target["id"]]
    return remaining, target

def update_wardrobe_item(
    items: Sequence[Mapping[str, Any]] | None,
    reference: Any,
    *,
    name: Any = None,
    description: Any = None,
    tags: Any = None,
    slot: Any = None,
    intimate: Any = None,
    precision: Any = None,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Patch one item in place; return the new list and the updated item."""

    normalized = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    target = _wardrobe_host.find_wardrobe_item(normalized, reference)
    if target is None:
        raise KeyError(_wardrobe_host.clean_wardrobe_text(reference, 80))
    if (
        name is None
        and description is None
        and tags is None
        and slot is None
        and intimate is None
        and precision is None
    ):
        raise _wardrobe_host.WardrobeError("没有需要修改的内容")
    timestamp = float(now if now is not None else time.time())
    updated: list[dict[str, Any]] = []
    for item in normalized:
        if item["id"] != target["id"]:
            updated.append(item)
            continue
        row = dict(item)
        if name is not None:
            clean_name = _wardrobe_host.clean_wardrobe_text(name, _wardrobe_host.WARDROBE_MAX_NAME)
            if clean_name:
                row["name"] = clean_name
        if description is not None:
            row["description"] = _wardrobe_host.clean_wardrobe_text(description, _wardrobe_host.WARDROBE_MAX_DESCRIPTION)
        if tags is not None:
            row["tags"] = _wardrobe_host.normalize_wardrobe_tags(tags)
        # 传空字符串/空表即清空该字段，这样「未分类」「不限场景」是可表达的。
        if slot is not None:
            row["slot"] = _wardrobe_host.normalize_wardrobe_slot(slot)
        if intimate is not None:
            row["intimate"] = _wardrobe_host.normalize_wardrobe_bool(intimate)
        if precision is not None:
            row["precision"] = _wardrobe_host.normalize_wardrobe_precision(precision)
        row["updated_at"] = timestamp
        updated.append(row)
    result = _wardrobe_host.normalize_wardrobe_items(updated)
    found = next((item for item in result if item["id"] == target["id"]), None)
    if found is None:
        raise _wardrobe_host.WardrobeError("修改后的条目无效")
    return result, found

def clear_wardrobe() -> tuple[list[dict[str, Any]], str]:
    """Return the empty wardrobe state."""

    return [], ""

def wardrobe_summary_lines(
    items: Sequence[Mapping[str, Any]] | None,
    *,
    limit: int = 0,
    description_limit: int = 60,
) -> list[str]:
    """Render numbered human-readable lines for command replies."""

    normalized = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    lines: list[str] = []
    for index, item in enumerate(normalized):
        if limit and index >= limit:
            break
        parts = [f"{index + 1}. {item['name']}"]
        labels: list[str] = []
        slot_label = _wardrobe_host.WARDROBE_SLOT_LABELS.get(str(item.get("slot") or ""), "")
        if slot_label:
            labels.append(slot_label)
        if item.get("intimate"):
            labels.append("贴身")
        if labels:
            parts.append(f"({'·'.join(labels)})")
        description = _wardrobe_host.clean_wardrobe_text(item.get("description"), description_limit)
        if description:
            parts.append(f"—— {description}")
        tags = list(item.get("tags") or [])
        if tags:
            parts.append(f"[{'/'.join(tags)}]")
        if item.get("source_kind") == _wardrobe_host.SOURCE_KIND_IMAGE:
            parts.append("(来自图片)")
        lines.append(" ".join(parts))
    return lines

def clean_prompt_limit(value: Any, default: int = _wardrobe_host.WARDROBE_PROMPT_MAX_CHARS) -> int:
    """把预算折成一个有限整数；非法输入（None / 非数字 / inf / NaN）落回默认值。

    与 :func:`wardrobe_decision.clean_length` 同一思路：预算来自配置或面板，
    宁可当成默认值，也不能让一次 TypeError/OverflowError 打断整轮注入。
    注意 `<= 0` 是**不限制**的哨兵语义（见 _truncate_block），这里原样保留。
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return int(number)

def truncate_wardrobe_text(text: str, limit: int) -> str:
    """把一段**完整段落**（含前言）压到 limit 以内。"""

    return _wardrobe_host._truncate_block(text, _wardrobe_host.clean_prompt_limit(limit))

def _truncate_block(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

def _slot_header(slot: str) -> str:
    # 刻意不用全角方括号做标题：仓库的 CI（scripts/ci_static_checks.py 的
    # raw_legacy_heading 规则）把提示词里的字面量方括号标题视为待淘汰的旧写法
    # 语法，只允许 canonical renderer 使用。这里用分隔线代替。
    return f"── {_wardrobe_host.WARDROBE_SLOT_LABELS.get(slot, '未分类')} ──"

def _wardrobe_notice(count: int) -> str:
    """截断提示。丢弃件数不管是条数上限还是字符预算造成的，都算在这里。"""

    return f"（另有 {count} 件未列出）"
