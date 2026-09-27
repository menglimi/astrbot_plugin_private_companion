# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any

try:
    from .wardrobe_shared import _wardrobe_host
except ImportError:  # 离线工具把本模块当顶层模块加载
    from wardrobe_shared import _wardrobe_host


def update_wardrobe_outfit(
    outfits: Sequence[Mapping[str, Any]] | None,
    reference: Any,
    *,
    name: Any = None,
    kind: Any = None,
    style: Any = None,
    items: Any = None,
    precision: Any = None,
    now: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Patch one outfit in place; return the new list and the updated outfit."""

    normalized = _wardrobe_host.normalize_wardrobe_outfits(list(outfits or ()))
    target = _wardrobe_host.find_wardrobe_outfit(normalized, reference)
    if target is None:
        raise KeyError(_wardrobe_host.clean_wardrobe_text(reference, 80))
    if name is None and kind is None and style is None and items is None and precision is None:
        raise _wardrobe_host.WardrobeError("没有需要修改的内容")
    timestamp = float(now if now is not None else time.time())
    updated: list[dict[str, Any]] = []
    for outfit in normalized:
        if outfit["id"] != target["id"]:
            updated.append(outfit)
            continue
        row = dict(outfit)
        if name is not None:
            clean_name = _wardrobe_host.clean_wardrobe_text(name, _wardrobe_host.WARDROBE_MAX_OUTFIT_NAME)
            if clean_name:
                row["name"] = clean_name
        if kind is not None:
            row["kind"] = _wardrobe_host.normalize_wardrobe_outfit_kind(kind) or row.get("kind", _wardrobe_host.OUTFIT_KIND_STYLE)
        if style is not None:
            row["style"] = _wardrobe_host.clean_wardrobe_multiline(style, _wardrobe_host.WARDROBE_MAX_OUTFIT_STYLE)
        if items is not None:
            row["items"] = _wardrobe_host.normalize_wardrobe_outfit({**row, "items": items})["items"]
        if precision is not None:
            row["precision"] = _wardrobe_host.normalize_wardrobe_precision(precision)
        row["updated_at"] = timestamp
        updated.append(row)
    result = _wardrobe_host.normalize_wardrobe_outfits(updated)
    found = next((outfit for outfit in result if outfit["id"] == target["id"]), None)
    if found is None:
        raise _wardrobe_host.WardrobeError("修改后的整套无效")
    return result, found

def _stable_index(seed: str, size: int) -> int:
    """Deterministic index in [0, size); the same seed always picks the same row."""

    if size <= 0:
        return 0
    digest = hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % size

def _seed_day_ordinal(seed: str) -> int | None:
    """Day ordinal when the seed starts with an ISO date, else ``None``."""

    head = str(seed or "")[:10]
    if len(head) != 10 or head[4] != "-" or head[7] != "-":
        return None
    try:
        return date(int(head[:4]), int(head[5:7]), int(head[8:10])).toordinal()
    except ValueError:
        return None

def _window_order(salt: str, index: int, size: int) -> list[int]:
    """Stable shuffled order for one rotation window."""

    return sorted(
        range(size),
        key=lambda slot: hashlib.sha256(f"{salt}|{index}|{slot}".encode("utf-8")).hexdigest(),
    )

def _rotation_index(seed: str, size: int, rotation_days: Any) -> int:
    """Pick a slot in a rotating wardrobe without repeating on two days running.

    Windows are aligned to natural weeks, so the default ``rotation_days=7``
    means "wear every outfit once per week".  ``rotation_days`` <= 1 — or a seed
    that carries no date — falls back to plain per-seed hashing, which keeps
    callers that pass no window (and the panel's hand-typed seeds) working as before.
    """

    if size <= 1:
        return 0
    text = str(seed or "")
    try:
        window = max(1, int(rotation_days))
    except (TypeError, ValueError):
        window = 1
    day = _wardrobe_host._seed_day_ordinal(text) if window > 1 else None
    if day is None:
        return _wardrobe_host._stable_index(text, size)
    # 日期只用来决定「第几个窗口、窗口里第几天」，洗牌顺序只看窗口号 ——
    # 否则每天都重新洗牌，等于每天随机抽一套。
    index, offset = divmod(day - 1, window)
    offset %= size
    salt = text[10:]
    order = _wardrobe_host._window_order(salt, index, size)
    # 整个窗口共用一份（可能微调过的）顺序：只在窗口第一天做对调的话，
    # 第二天仍用未对调的序列，反而会和第一天撞衫。
    previous = _wardrobe_host._window_order(salt, index - 1, size)
    if order[0] == previous[(window - 1) % size]:
        # 换窗口的第一天撞上昨天那套：和下一个位置对调，衔接处也不重复。
        order[0], order[1] = order[1], order[0]
    return order[offset]

def _item_priority(row: Mapping[str, Any], *, fresh: bool = True) -> int:
    """决策层 priority 在散件上的取值：已分类 > 未分类，有描述 > 无描述。

    冷却（`recent_ids`）也参与打分，但真正的"绝不连穿两天"由
    :func:`_pick_for_slot` 的**硬过滤**保证，见那里的说明。
    """

    return _wardrobe_host.score_priority(
        classified=bool(str(row.get("slot") or "")),
        described=bool(str(row.get("description") or "").strip()),
        fresh=fresh,
    )

def _pick_for_slot(
    candidates: Sequence[Mapping[str, Any]],
    *,
    seed: str,
    recent_ids: Collection[str],
) -> Mapping[str, Any] | None:
    """Pick one item deterministically, preferring rows not worn recently.

    冷却仍然是硬过滤：只要还有没穿过的，就只从没穿过的里挑 —— 打分不能把
    "连着两天穿同一件"重新放回来。priority 决定的是**池内顺序**：有描述的排在
    没描述的前面，其余仍按内容排序而不是按 id（即使 id 因某种原因不稳定，
    例如手工改过配置，挑选顺序也保持一致，不会每轮换一套衣服）。
    """

    if not candidates:
        return None
    ordered = sorted(
        candidates,
        key=lambda row: (
            -_wardrobe_host._item_priority(row, fresh=str(row.get("id") or "") not in recent_ids),
            str(row.get("name") or ""),
            str(row.get("description") or ""),
            str(row.get("id") or ""),
        ),
    )
    fresh = [row for row in ordered if str(row.get("id") or "") not in recent_ids]
    pool = fresh or ordered
    return pool[_wardrobe_host._stable_index(seed, len(pool))]

def _outfit_profile_text(item: Mapping[str, Any]) -> str:
    name = _wardrobe_host.clean_wardrobe_text(item.get("name"), _wardrobe_host.WARDROBE_MAX_NAME)
    detail = _wardrobe_host.clean_wardrobe_text(item.get("description"), 80)
    if name and detail:
        return f"{name}，{detail}"
    return name or detail

def apply_wardrobe_draft(
    items: Sequence[Mapping[str, Any]] | None,
    outfits: Sequence[Mapping[str, Any]] | None,
    draft: Mapping[str, Any] | None,
    *,
    asset_id: Any = "",
    source: Any = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Route one understanding-layer draft into items / outfits.

    返回 (items, outfits, outcome)，outcome 形如
    {ok, kind, name, replaced, limit, error}。

    命令路径（聊天里发图）与草稿队列（批量确认）共用这一处分流逻辑，
    所以"一张图到底进哪个库"只有一个实现。
    """

    current_items = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    current_outfits = _wardrobe_host.normalize_wardrobe_outfits(list(outfits or ()))
    payload = draft if isinstance(draft, Mapping) else {}
    raw_kind = _wardrobe_host.clean_wardrobe_text(payload.get("kind"), 32)
    kind = _wardrobe_host.normalize_wardrobe_image_kind(raw_kind)
    name = _wardrobe_host.clean_wardrobe_text(payload.get("name"), _wardrobe_host.WARDROBE_MAX_NAME)
    clean_asset = _wardrobe_host.clean_wardrobe_text(asset_id, 80)
    asset_ids = [clean_asset] if clean_asset else None
    clean_source = _wardrobe_host.clean_wardrobe_text(source, _wardrobe_host.WARDROBE_MAX_SOURCE)
    outcome: dict[str, Any] = {
        "ok": False,
        "kind": kind,
        "name": name,
        "replaced": False,
        "limit": False,
        "error": "",
    }

    if raw_kind and not kind:
        outcome["error"] = f"无法识别的类型：{raw_kind}"
        return current_items, current_outfits, outcome
    if not kind:
        # 缺类型＝旧格式，按散件处理（向后兼容）
        kind = _wardrobe_host.WARDROBE_IMAGE_KIND_ITEM
        outcome["kind"] = kind

    if kind in (_wardrobe_host.WARDROBE_IMAGE_KIND_OUTFIT, _wardrobe_host.WARDROBE_IMAGE_KIND_REFERENCE):
        # 精确同名：草稿带的是「名字」，不是用户点名引用（见 find_*_by_exact_name）
        existing = _wardrobe_host.find_wardrobe_outfit_by_exact_name(current_outfits, name)
        try:
            current_outfits, stored = _wardrobe_host.add_wardrobe_outfit(
                current_outfits,
                name=name,
                kind=_wardrobe_host.OUTFIT_KIND_STYLE,
                style=_wardrobe_host.clean_wardrobe_text(payload.get("description"), _wardrobe_host.WARDROBE_MAX_DESCRIPTION),
                asset_ids=asset_ids,
                ownership=(
                    _wardrobe_host.OWNERSHIP_REFERENCE
                    if kind == _wardrobe_host.WARDROBE_IMAGE_KIND_REFERENCE
                    else _wardrobe_host.OWNERSHIP_OWNED
                ),
            )
        except _wardrobe_host.WardrobeLimitError as exc:
            outcome.update(error=str(exc), limit=True)
            return current_items, current_outfits, outcome
        except _wardrobe_host.WardrobeError as exc:
            outcome["error"] = f"{name}：{exc}"
            return current_items, current_outfits, outcome
        outcome.update(ok=True, name=stored["name"], replaced=bool(existing))
        return current_items, current_outfits, outcome

    if kind != _wardrobe_host.WARDROBE_IMAGE_KIND_ITEM:
        outcome["error"] = f"未知类型：{kind or '（空）'}"
        return current_items, current_outfits, outcome

    description = _wardrobe_host.clean_wardrobe_text(payload.get("description"), _wardrobe_host.WARDROBE_MAX_DESCRIPTION)
    existing_item = _wardrobe_host.find_wardrobe_item_by_exact_name(current_items, name)
    slot = _wardrobe_host.normalize_wardrobe_slot(payload.get("slot")) or _wardrobe_host.infer_wardrobe_slot(name, description)
    try:
        current_items, stored_item = _wardrobe_host.add_wardrobe_item(
            current_items,
            name=name,
            description=description,
            tags=payload.get("tags"),
            slot=slot,
            source=clean_source,
            source_kind=_wardrobe_host.SOURCE_KIND_IMAGE if clean_source else _wardrobe_host.SOURCE_KIND_MANUAL,
            asset_ids=asset_ids,
        )
    except _wardrobe_host.WardrobeLimitError as exc:
        outcome.update(error=str(exc), limit=True)
        return current_items, current_outfits, outcome
    except _wardrobe_host.WardrobeError as exc:
        outcome["error"] = f"{name}：{exc}"
        return current_items, current_outfits, outcome
    outcome.update(ok=True, name=stored_item["name"], replaced=bool(existing_item))
    return current_items, current_outfits, outcome

def _render_picked_outfit(picked: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for slot in (*_wardrobe_host._RULE_PICK_ORDER, ""):
        bucket = [row for row in picked if str(row.get("slot") or "") == slot]
        if not bucket:
            continue
        # 注意：这里保持「其他」而不是与 _slot_header 的「未分类」统一 ——
        # 上游测试 test_wardrobe_without_any_slot_still_resolves_an_outfit 钉的就是这个词，
        # 为一句措辞去改既有测试不划算（已在评审文档里记为 wontfix）。
        label = _wardrobe_host.WARDROBE_SLOT_LABELS.get(slot, "其他")
        for row in bucket:
            marker = "（贴身）" if row.get("intimate") else ""
            detail = _wardrobe_host.clean_wardrobe_text(row.get("description"), 120)
            text = f"{row.get('name')}{marker}"
            lines.append(f"{label}：{text}——{detail}" if detail else f"{label}：{text}")
    return "\n".join(lines)

def render_worn_items(picked: Sequence[Mapping[str, Any]] | None) -> str:
    """Render the items a character is *explicitly* wearing right now.

    与 :func:`select_wardrobe_outfit` 的规则裁决结果共用同一个渲染格式
    （:func:`_render_picked_outfit`），避免「今天这一身」与「本会话指定穿这一身」
    两处措辞各自漂移。
    """

    return _wardrobe_host._render_picked_outfit(list(picked or ()))

def select_wardrobe_outfit(
    items: Sequence[Mapping[str, Any]] | None,
    outfits: Sequence[Mapping[str, Any]] | None = None,
    *,
    scene: Any = "",
    seed: str = "",
    recent_ids: Collection[str] | None = None,
    rotation_days: Any = 0,
) -> dict[str, Any]:
    """Compose one outfit **without calling a model**.

    Resolution order (design doc 5.4 / 6.5):

    1. bundle 整套命中 -> 用它的组成（多套之间按 rotation_days 窗口轮换）；
    2. style  整套命中 -> 用它的风格描述（同样轮换）；
    3. 散件兜底        -> 每个部位取一件。

    The result is always internally coherent because at most one item is taken
    per slot.  Rich layering (外套在内搭外面、泳衣不叠内衣) is the model-backed
    generator's job; this path is the deterministic fallback and the panel's
    offline preview.
    """

    normalized_items = _wardrobe_host.normalize_wardrobe_items(list(items or ()))
    normalized_outfits = _wardrobe_host.normalize_wardrobe_outfits(list(outfits or ()))
    recent = {str(entry) for entry in (recent_ids or ())}
    clean_scene = _wardrobe_host.clean_wardrobe_text(scene, _wardrobe_host.WARDROBE_MAX_TAG).casefold()
    base_seed = f"{seed}|{clean_scene}"

    by_id = {str(item["id"]): item for item in normalized_items}
    usable = list(normalized_items)

    def _compose(source: str, picked: list[Mapping[str, Any]], style_text: str = "") -> dict[str, Any]:
        profile: dict[str, str] = {}
        for item in picked:
            # 贴身件只进对话注入，不进生图投影。
            if item.get("intimate"):
                continue
            field = _wardrobe_host.SLOT_PROFILE_FIELDS.get(str(item.get("slot") or ""), "")
            if field and field not in profile:
                profile[field] = _wardrobe_host._outfit_profile_text(item)
        body = _wardrobe_host._render_picked_outfit(picked)
        joined = "\n".join(part for part in (style_text.strip(), body) if part)
        return {
            "source": source,
            "scene": clean_scene,
            "style": style_text.strip(),
            "prompt_text": joined,
            "profile": profile,
            "picked": [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "slot": str(item.get("slot") or ""),
                    "intimate": bool(item.get("intimate")),
                }
                for item in picked
            ],
            "look_id": "",
            "outfit_name": "",
        }

    ordered_outfits = sorted(normalized_outfits, key=lambda row: str(row.get("id") or ""))

    # 1) bundle 整套优先：它是用户明确拼好的那一套。候选可能不止一套，按种子轮换
    #    挑选 —— 否则排序最靠前的那套会永远霸占衣柜，其余整套等于不存在。
    bundles: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = []
    for outfit in ordered_outfits:
        if outfit.get("kind") != _wardrobe_host.OUTFIT_KIND_BUNDLE:
            continue
        picked = [by_id[key] for key in outfit.get("items") or () if key in by_id]
        if not picked:
            # 引用的散件都被删了 —— 跳过，继续找下一套，别返回空壳。
            continue
        bundles.append((outfit, picked))
    if bundles:
        slot = _wardrobe_host._rotation_index(f"{base_seed}|bundle", len(bundles), rotation_days)
        chosen, picked = bundles[slot]
        result = _compose("bundle", picked, str(chosen.get("style") or ""))
        result["look_id"] = f"bundle-{chosen['id']}"
        result["outfit_name"] = str(chosen.get("name") or "")
        return result

    # 2) style 整套：只有描述，留给模型发挥。
    styles = [
        outfit
        for outfit in ordered_outfits
        if outfit.get("kind") == _wardrobe_host.OUTFIT_KIND_STYLE
        and str(outfit.get("style") or "").strip()
    ]
    if styles:
        chosen = styles[_wardrobe_host._rotation_index(f"{base_seed}|style", len(styles), rotation_days)]
        result = _compose("style", [], str(chosen.get("style") or ""))
        result["look_id"] = f"style-{chosen['id']}"
        result["outfit_name"] = str(chosen.get("name") or "")
        return result

    # 3) 散件兜底：每个「部位 + 是否贴身」组合最多一件，因此天然自洽。
    #
    # 贴身件是一条**独立轴**而不是互相竞争的部位：穿开衫的同时也穿内衣，
    # 所以 (upper, 非贴身) 与 (upper, 贴身) 各自挑一件，不能合成一组 —— 否则
    # 内衣永远抢不过外衣，贴身层就形同虚设。
    #
    # whole（连衣裙/连体）与「上身 + 下身」是两种互斥的外衣穿法。这里用同一个
    # 种子在两者之间做**确定性**选择：如果无条件让 whole 压制上下装，一件连衣裙
    # 就会永远霸占衣柜，其他上衣裤子再也穿不上。
    has_whole_pool = any(
        str(item.get("slot") or "") == _wardrobe_host.SLOT_WHOLE and not item.get("intimate")
        for item in usable
    )
    has_separates_pool = any(
        str(item.get("slot") or "") in (_wardrobe_host.SLOT_UPPER, _wardrobe_host.SLOT_LOWER) and not item.get("intimate")
        for item in usable
    )
    if has_whole_pool and has_separates_pool:
        use_whole = _wardrobe_host._stable_index(f"{base_seed}|whole-vs-separates", 2) == 0
    else:
        use_whole = has_whole_pool

    picked_items: list[Mapping[str, Any]] = []
    for slot in _wardrobe_host._RULE_PICK_ORDER:
        for intimate in (False, True):
            if intimate and slot not in (_wardrobe_host.SLOT_UPPER, _wardrobe_host.SLOT_LOWER):
                # 只有上下身有贴身件；鞋袜、配件没有这个概念。
                continue
            if not intimate:
                # 外衣层：连衣裙与上下装二选一，由上面的种子决定。
                if slot == _wardrobe_host.SLOT_WHOLE and not use_whole:
                    continue
                if slot in (_wardrobe_host.SLOT_UPPER, _wardrobe_host.SLOT_LOWER) and use_whole:
                    continue
            candidates = [
                item
                for item in usable
                if str(item.get("slot") or "") == slot
                and bool(item.get("intimate")) is intimate
            ]
            chosen_item = _wardrobe_host._pick_for_slot(
                candidates,
                seed=f"{base_seed}|{slot}|{int(intimate)}",
                recent_ids=recent,
            )
            if chosen_item is not None:
                picked_items.append(chosen_item)

    # 未分类的衣物没有部位可依据，正常不参与组合。但如果整个衣柜都没有部位
    # 信息（老配置、或用户还没整理过），空手而归对用户毫无用处 —— 这时退一步
    # 取几件未分类的，保证仍然能解析出一套可注入的着装。
    if not picked_items:
        unclassified = [
            item for item in usable if not str(item.get("slot") or "")
        ]
        # 兜底同样走 priority：先保有几句话可说的，再按 id 稳定排序 ——
        # 同优先级时的顺序与旧实现逐字一致。
        unclassified = sorted(
            unclassified,
            key=lambda row: (-_wardrobe_host._item_priority(row), str(row.get("id") or "")),
        )
        picked_items = unclassified[:3]
    result = _compose("rule", picked_items)
    picked_ids = "-".join(str(item.get("id") or "") for item in picked_items)
    digest = hashlib.sha256(f"{base_seed}|{picked_ids}".encode("utf-8")).hexdigest()
    result["look_id"] = f"rule-{digest[:12]}"
    return result

def build_wardrobe_outfit_request(
    items: Sequence[Mapping[str, Any]] | None,
    outfits: Sequence[Mapping[str, Any]] | None = None,
    *,
    tendency: Any = "",
    scene: Any = "",
    weather: Any = "",
    recent_names: Sequence[Any] | None = None,
    max_items: int = _wardrobe_host.WARDROBE_PROMPT_MAX_ITEMS,
    max_chars: int = _wardrobe_host.WARDROBE_PROMPT_MAX_CHARS,
) -> str:
    """Build the outfit-generator request body.

    Pure and side-effect free, so the panel can show exactly what will be sent
    instead of approximating it.
    """

    owned_items = [
        item
        for item in _wardrobe_host.normalize_wardrobe_items(list(items or ()))
        if str(item.get("ownership") or _wardrobe_host.OWNERSHIP_OWNED) == _wardrobe_host.OWNERSHIP_OWNED
    ]
    inventory = _wardrobe_host.render_wardrobe_block(
        "", owned_items, max_items=max_items, max_chars=max_chars
    )
    heading = "衣柜里的具体衣物："
    if inventory.startswith(heading):
        inventory = inventory[len(heading):].strip()

    lines = [
        "你在为角色决定这次对话要穿的服装。只依据下面的衣柜与场合信息选择，"
        "不要编造衣柜里没有的衣物。",
        "",
        "── 场合 ──",
        f"场景：{_wardrobe_host.clean_wardrobe_text(scene, 40) or 'daily'}",
    ]
    clean_weather = _wardrobe_host.clean_wardrobe_text(weather, 120)
    if clean_weather:
        lines.append(f"天气：{clean_weather}")

    clean_tendency = _wardrobe_host.normalize_wardrobe_tendency(tendency)
    if clean_tendency:
        lines += ["", "── 整体服饰倾向 ──", clean_tendency]

    style_hints = [
        str(outfit.get("style") or "").strip()
        for outfit in _wardrobe_host.normalize_wardrobe_outfits(list(outfits or ()))
        if outfit.get("kind") == _wardrobe_host.OUTFIT_KIND_STYLE
        and str(outfit.get("style") or "").strip()
    ]
    if style_hints:
        lines += ["", "── 可参考的整体风格 ──", "；".join(style_hints)]

    if inventory.strip():
        lines += ["", "── 衣柜 ──", inventory.strip()]

    recent = [
        _wardrobe_host.clean_wardrobe_text(name, _wardrobe_host.WARDROBE_MAX_NAME)
        for name in (recent_names or ())
    ]
    recent = [name for name in recent if name]
    if recent:
        lines += ["", "── 最近穿过（尽量避开）──", "、".join(recent)]

    lines += [
        "",
        "── 规则 ──",
        "1. 每个部位最多选一件；选了整身（连衣裙/连体）就不要再选上装与下装。",
        "2. 标注为贴身的衣物单独选，上下一共最多各一件。",
        "3. 搭配要贴合场景与天气：冷天考虑加外套，运动场合选运动装，居家选舒适款。",
        "4. 只输出一个 JSON 对象，不要解释，也不要加代码块标记。",
        "5. 没有把握的部位留空字符串，不要硬凑。",
        "",
        "── 输出格式 ──",
        '{"top": "", "outer": "", "bottom": "", "footwear": "", "accessory": "", '
        '"underwear_top": "", "underwear_bottom": "", "palette": "", "silhouette": "", '
        '"summary": ""}',
    ]
    return _wardrobe_host._truncate_block("\n".join(lines), _wardrobe_host.WARDROBE_OUTFIT_REQUEST_LIMIT)

def parse_wardrobe_outfit_reply(text: Any) -> dict[str, Any] | None:
    """Parse a generator reply into a normalized outfit payload.

    Accepts a bare JSON object or one wrapped in a Markdown code fence, because
    models add fences even when told not to.  Returns None when nothing usable
    is found, so the caller can fall back to the rule selector instead of
    injecting an empty outfit.
    """

    raw = str(text or "").strip()
    if not raw:
        return None

    candidate = raw
    if candidate.startswith(_wardrobe_host._FENCE):
        parts = candidate.split("\n")
        if parts and parts[0].startswith(_wardrobe_host._FENCE):
            parts = parts[1:]
        if parts and parts[-1].strip().startswith(_wardrobe_host._FENCE):
            parts = parts[:-1]
        candidate = "\n".join(parts).strip()

    payload: Any = None
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if 0 <= start < end:
            try:
                payload = json.loads(candidate[start : end + 1])
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
    if not isinstance(payload, Mapping):
        return None

    fields: dict[str, str] = {}
    for key in _wardrobe_host.OUTFIT_REPLY_FIELDS:
        limit = (
            _wardrobe_host.WARDROBE_OUTFIT_SUMMARY_LIMIT
            if key == "summary"
            else _wardrobe_host.WARDROBE_OUTFIT_FIELD_LIMIT
        )
        value = _wardrobe_host.clean_wardrobe_text(payload.get(key), limit)
        if value:
            fields[key] = value

    # 至少要有一件真实衣物：只有 summary、只有配色或只有轮廓等于什么都没生成，
    # 让调用方回退规则选择器，而不是注入一段没有衣服的"描述"。
    if not any(fields.get(key) for key in _wardrobe_host._OUTFIT_GARMENT_FIELDS):
        return None
    return fields

def render_generated_outfit(payload: Mapping[str, Any] | None) -> str:
    """Render a parsed generator payload as the injected outfit body."""

    if not isinstance(payload, Mapping):
        return ""
    lines: list[str] = []
    summary = _wardrobe_host.clean_wardrobe_text(payload.get("summary"), _wardrobe_host.WARDROBE_OUTFIT_SUMMARY_LIMIT)
    if summary:
        lines.append(summary)
    for key in _wardrobe_host._OUTFIT_RENDER_ORDER:
        value = _wardrobe_host.clean_wardrobe_text(payload.get(key), _wardrobe_host.WARDROBE_OUTFIT_FIELD_LIMIT)
        if not value:
            continue
        marker = "（贴身）" if key in _wardrobe_host.OUTFIT_INTIMATE_FIELDS else ""
        lines.append(f"{_wardrobe_host._FIELD_LABELS_ZH.get(key, key)}：{value}{marker}")
    return "\n".join(lines)

def outfit_photo_profile_from_items(
    items: Sequence[Mapping[str, Any]] | None,
) -> dict[str, str]:
    """Photo projection of *explicitly specified* items (dialogue outfit).

    与规则裁决路径共用 :data:`SLOT_PROFILE_FIELDS` 与 :func:`_outfit_profile_text`，
    贴身件照旧不进照片提示词（本会话换装也不行）。
    """

    profile: dict[str, str] = {}
    for item in items or ():
        if item.get("intimate"):
            continue
        field = _wardrobe_host.SLOT_PROFILE_FIELDS.get(str(item.get("slot") or ""), "")
        if field and field not in profile:
            profile[field] = _wardrobe_host._outfit_profile_text(item)
    return profile

def outfit_photo_profile(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Photo projection of a generated outfit; intimate fields are dropped.

    This mirrors :data:`SLOT_PROFILE_FIELDS` for the rule path, and is the
    mechanism that keeps intimate garments out of photo generation.
    """

    if not isinstance(payload, Mapping):
        return {}
    profile: dict[str, str] = {}
    for key in _wardrobe_host.OUTFIT_PHOTO_FIELDS:
        value = _wardrobe_host.clean_wardrobe_text(payload.get(key), _wardrobe_host.WARDROBE_OUTFIT_FIELD_LIMIT)
        if value:
            profile[key] = value
    return profile
