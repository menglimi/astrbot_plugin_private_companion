"""Metadata catalog for full-site search and global feature overview.

Automatically inspects `_conf_schema.json`, native extension schemas under
`astrbot_host/native_extensions/*/settings-schema.json`, and registered model
tasks so global search never drifts out of sync with backend schemas.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONF_SCHEMA_PATH = PLUGIN_ROOT / "_conf_schema.json"
NATIVE_EXTENSIONS_ROOT = PLUGIN_ROOT / "astrbot_host" / "native_extensions"

NATIVE_EXTENSION_META: dict[str, dict[str, str]] = {
    "game": {
        "name": "游戏棋桌",
        "description": "管理聊天内游戏、网页房间与棋类引擎。",
    },
    "live": {
        "name": "直播与表演",
        "description": "连接 VTube Studio、直播间、OBS 和字幕，调整角色的表演。",
    },
    "reality": {
        "name": "现实触及",
        "description": "连接手机、摄像头和家居设备；在当前会话中按授权使用。",
    },
    "together": {
        "name": "一起房间",
        "description": "配置实时房间、语音、一起看和一起阅读。",
    },
}

# Canonical Chinese metadata for V2 model tasks (kept synchronized with model routing).
CANONICAL_MODEL_TASKS: tuple[dict[str, Any], ...] = (
    {"kind": "conversation.reply", "group": "对话", "label": "对话回复",
     "description": "AstrBot 核心回复与 V2 附加提示", "aliases": ["核心回复", "主链回复"]},
    {"kind": "conversation.poke_reply", "group": "对话", "label": "戳一戳回复",
     "description": "收到戳一戳时生成简短自然的回应", "aliases": ["拍一拍回复", "戳一戳模型"]},
    {"kind": "conversation.context_image_caption", "group": "对话", "label": "图片摘要",
     "description": "为已授权的历史图片提取可见内容", "aliases": ["历史图片摘要", "看图摘要"]},
    {"kind": "conversation.image_narration", "group": "对话", "label": "当前图片转述",
     "description": "为本轮已授权图片生成简短文字描述", "aliases": ["图片理解模型", "识图转述"]},
    {"kind": "conversation.video_narration", "group": "对话", "label": "原生视频转述",
     "description": "将原视频或压缩视频交给原生视频或视觉中转模型理解", "aliases": ["视频理解模型", "看视频"]},
    {"kind": "screen.interpretation", "group": "感知", "label": "屏幕解读",
     "description": "分析已授权截图与摄像头单帧，需要支持图片的模型", "aliases": ["看屏模型", "截图理解"]},
    {"kind": "conversation.image_review", "group": "感知", "label": "图片审核",
     "description": "检查群聊成图的可见内容", "aliases": ["群图审核", "生图审核"]},
    {"kind": "reality.text", "group": "感知", "label": "现实提醒文案",
     "description": "现实提醒、唤醒回执和短回复的文字生成", "aliases": ["闹钟文案", "叫醒文案"]},
    {"kind": "proactive.plan", "group": "主动与整理", "label": "主动联系规划",
     "description": "双阶段模式下判断时机并整理新回合切口", "aliases": ["主动规划模型"]},
    {"kind": "proactive.compose", "group": "主动与整理", "label": "主动联系写作",
     "description": "按所选方式生成主动消息正文", "aliases": ["主动消息写作"]},
    {"kind": "proactive.judge", "group": "主动与整理", "label": "主动联系复核",
     "description": "复核主动消息是否符合当前人格与来源", "aliases": ["主动消息复核"]},
    {"kind": "memory.reflect", "group": "主动与整理", "label": "事实整理",
     "description": "从已授权素材整理认识", "aliases": ["记忆反思模型", "事实反思"]},
    {"kind": "calendar.plan", "group": "生活与成长", "label": "日程规划",
     "description": "基于已有目标规划下一步", "aliases": ["日程模型", "作息规划"]},
    {"kind": "role.state.maintain", "group": "生活与成长", "label": "角色状态",
     "description": "维护角色的连续生活状态", "aliases": ["状态维护模型"]},
    {"kind": "role.reading.consume", "group": "生活与成长", "label": "阅读理解",
     "description": "阅读实际提供的原文", "aliases": ["读书模型", "书架阅读"]},
    {"kind": "role.growth.progress", "group": "生活与成长", "label": "成长进展",
     "description": "根据已完成确认提出进展", "aliases": ["技能成长模型"]},
    {"kind": "role.journal.diary", "group": "私人书写", "label": "日记",
     "description": "保存私人书写", "aliases": ["写日记模型"]},
    {"kind": "role.journal.dream", "group": "私人书写", "label": "梦境",
     "description": "保存梦境记录", "aliases": ["写梦境模型"]},
    {"kind": "role.journal.derive", "group": "私人书写", "label": "书写整理",
     "description": "整理已有文字素材", "aliases": ["素材整理"]},
    {"kind": "role.journal.select", "group": "私人书写", "label": "素材选择",
     "description": "选择本次书写素材", "aliases": ["日记选材"]},
    {"kind": "role.expression.learn", "group": "学习", "label": "表达学习",
     "description": "从当前会话的原始素材提炼表达规则", "aliases": ["语气学习模型"]},
    {"kind": "role.expression.review", "group": "学习", "label": "回复表达复核",
     "description": "发送前检查规则应用是否自然", "aliases": ["表达复核"]},
    {"kind": "role.expression.contact_review", "group": "学习", "label": "主动表达复核",
     "description": "主动消息发送前检查表达", "aliases": ["主动表达检查"]},
    {"kind": "role.self_review", "group": "学习", "label": "每日复盘",
     "description": "从近期执行记录提炼调整", "aliases": ["自我复盘模型", "每日反思"]},
    {"kind": "content.authoring.draft", "group": "内容工作", "label": "内容起草",
     "description": "根据用户要求与已有素材创作或续写作品", "aliases": ["创作起草", "写小说模型"]},
)

_CANONICAL_TASK_BY_KIND = {item["kind"]: item for item in CANONICAL_MODEL_TASKS}


def _live_group(key: str) -> tuple[str, str]:
    if re.match(r"^subtitle_", key):
        return "subtitle", "字幕与显示"
    if re.match(r"^mouth_", key):
        return "mouth", "语音嘴型"
    if re.match(r"^soullink_", key):
        return "soullink", "实时表演"
    if re.match(r"^(autonomous_l2d|l2d_)", key):
        return "l2d", "Live2D 表情"
    if re.match(r"^twitch_", key):
        return "twitch", "Twitch 直播"
    if re.match(r"^(obs_|l2dstudio_)", key):
        return "obs", "OBS 与推流"
    if re.match(r"^(private_companion_|live_memory_)", key):
        return "memory", "关系与直播记忆"
    if re.match(r"^(bilibili_|bili_live_|laplace_|part_id$|area_id$)", key):
        return "bilibili", "B站直播"
    return "connection", "连接与基本设置"


def _walk_schema_leaves(
    schema: dict[str, Any],
    *,
    prefix: str = "",
    section: str = "",
    section_title: str = "",
    section_hint: str = "",
    out: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if out is None:
        out = []
    for key, raw in schema.items():
        if not isinstance(raw, dict):
            continue
        path = f"{prefix}.{key}" if prefix else key
        field_type = raw.get("type")
        if field_type == "object" and isinstance(raw.get("items"), dict):
            child_section = section or key
            child_title = str(raw.get("description") or raw.get("title") or key)
            child_hint = str(raw.get("hint") or section_hint or "")
            _walk_schema_leaves(
                raw["items"],
                prefix=path,
                section=child_section,
                section_title=child_title,
                section_hint=child_hint,
                out=out,
            )
            continue
        if raw.get("invisible") is True:
            continue
        out.append({
            "path": path,
            "section": section or key,
            "section_title": section_title or str(raw.get("description") or key),
            "section_hint": section_hint,
            "field": key,
            "label": str(raw.get("description") or raw.get("title") or key),
            "description": str(raw.get("description") or ""),
            "hint": str(raw.get("hint") or ""),
            "type": str(field_type or "string"),
            "default": raw.get("default") if isinstance(raw.get("default"), (bool, int, float)) else None,
        })
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@lru_cache(maxsize=4)
def _cached_plugin_schema_items(mtime_ns: int) -> tuple[dict[str, Any], ...]:
    _ = mtime_ns
    schema = _read_json(CONF_SCHEMA_PATH)
    return tuple(_walk_schema_leaves(schema))


def plugin_schema_visible_items() -> list[dict[str, Any]]:
    try:
        mtime_ns = CONF_SCHEMA_PATH.stat().st_mtime_ns
    except OSError:
        return []
    return [dict(item) for item in _cached_plugin_schema_items(mtime_ns)]


def _walk_extension_items(ext_id: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def visit(mapping: dict[str, Any], prefix: str, group_id: str, group_title: str) -> None:
        for key, raw in mapping.items():
            if not isinstance(raw, dict):
                continue
            path = f"{prefix}.{key}" if prefix else key
            field_type = raw.get("type")
            if field_type == "object" and isinstance(raw.get("items"), dict):
                next_group = group_id or key
                next_title = str(raw.get("description") or raw.get("title") or key)
                visit(raw["items"], path, next_group, next_title)
                continue
            if raw.get("invisible") is True or "type" not in raw:
                continue
            if not group_id:
                gid, gtitle = _live_group(key) if ext_id == "live" else ("general", "基本设置")
            else:
                gid, gtitle = group_id, group_title
            items.append({
                "path": path,
                "group": gid,
                "group_title": gtitle,
                "label": str(raw.get("description") or raw.get("title") or key),
                "hint": str(raw.get("hint") or ""),
                "type": str(field_type or "string"),
            })

    visit(schema, "", "", "")
    return items


@lru_cache(maxsize=8)
def _cached_native_extension(ext_id: str, mtime_ns: int) -> dict[str, Any]:
    _ = mtime_ns
    schema_path = NATIVE_EXTENSIONS_ROOT / ext_id / "settings-schema.json"
    schema = _read_json(schema_path)
    meta = NATIVE_EXTENSION_META.get(ext_id, {"name": ext_id, "description": ""})
    items = _walk_extension_items(ext_id, schema)
    bool_count = sum(1 for item in items if item.get("type") == "bool")
    return {
        "id": ext_id,
        "name": meta["name"],
        "description": meta["description"],
        "field_count": len(items),
        "bool_count": bool_count,
        "items": items,
    }


def native_extension_schema_catalog() -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for ext_id in sorted(NATIVE_EXTENSION_META):
        schema_path = NATIVE_EXTENSIONS_ROOT / ext_id / "settings-schema.json"
        if not schema_path.is_file():
            continue
        try:
            mtime_ns = schema_path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _cached_native_extension(ext_id, mtime_ns)
        catalog.append({
            **cached,
            "items": [dict(item) for item in cached["items"]],
        })
    return catalog


def _read_config_bool(config: Any, dotted_path: str, default: Any) -> bool | None:
    if not isinstance(config, dict):
        return bool(default) if isinstance(default, bool) else None
    cursor: Any = config
    for part in dotted_path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return bool(default) if isinstance(default, bool) else None
        cursor = cursor[part]
    if isinstance(cursor, bool):
        return cursor
    return bool(default) if isinstance(default, bool) else None


def registered_model_tasks(controller: Any = None) -> list[dict[str, Any]]:
    """Return canonical + any dynamically registered runtime model tasks."""
    tasks: list[dict[str, Any]] = [dict(item) for item in CANONICAL_MODEL_TASKS]
    seen = {item["kind"] for item in tasks}
    session = None
    if controller is not None:
        session = getattr(controller, "session", None) or getattr(controller, "last_session", None)
    router = getattr(getattr(session, "application", None), "router", None)
    profiles = getattr(router, "profiles", None)
    if isinstance(profiles, dict):
        for kind, profile in profiles.items():
            if not isinstance(kind, str) or not kind or kind in seen:
                continue
            seen.add(kind)
            canonical = _CANONICAL_TASK_BY_KIND.get(kind, {})
            caps = list(getattr(profile, "capabilities", ()) or ())
            tasks.append({
                "kind": kind,
                "group": canonical.get("group", "其他任务"),
                "label": canonical.get("label", kind),
                "description": canonical.get("description", "V2 已注册模型任务"),
                "capabilities": caps,
                "aliases": list(canonical.get("aliases", ())),
            })
    return tasks


def performance_tuning_catalog() -> list[dict[str, Any]]:
    """Return searchable metadata for performance profiles, tuning fields, and DB compaction."""
    from companion_v2.storage.tuning_config import PROFILE_CATALOG, TUNING_FIELDS

    entries: list[dict[str, Any]] = [
        {
            "key": "performance_tuning_profile",
            "label": "性能调优档位（自动 / 省内存 / 平衡 / 高性能 / 兼容稳妥）",
            "hint": "按本机内存与磁盘特点选择五档预设，支持硬件自动推荐、机械硬盘兼容与防爆内存保护。",
            "aliases": [
                "性能调优", "性能档位", "省内存", "低配机", "平衡推荐", "高性能",
                "兼容稳妥", "机械硬盘", "HDD", "NAS", "卡顿", "防卡顿",
                *[p.label for p in PROFILE_CATALOG],
                *[p.id for p in PROFILE_CATALOG],
            ],
            "target": "tuning",
        },
        {
            "key": "storage_database_health_compaction",
            "label": "数据库体检与一键瘦身",
            "hint": "查看主库与副库体积、空闲页与重复上下文占比，执行在线常规无损瘦身或全量深度重组释放空间。",
            "aliases": [
                "数据库瘦身", "数据库体检", "一键瘦身", "瘦身", "体检", "压缩",
                "释放空间", "清理空闲页", "VACUUM", "SQLite 瘦身", "重复上下文去重", "冷遥测归档",
            ],
            "target": "storage",
        },
    ]
    for spec in TUNING_FIELDS:
        entries.append({
            "key": spec.key,
            "label": spec.label,
            "hint": spec.effect_hint,
            "aliases": list(spec.aliases),
            "target": "tuning",
        })
    return entries


def build_search_catalog(controller: Any = None) -> dict[str, Any]:
    """Build full searchable metadata payload without exposing secrets."""
    config = getattr(controller, "config", None) if controller is not None else None
    plugin_items = plugin_schema_visible_items()
    for item in plugin_items:
        # Expose live value ONLY for safe boolean toggles; never leak secrets/tokens/keys.
        if item.get("type") == "bool":
            item["current_value"] = _read_config_bool(config, item["path"], item.get("default"))
    extensions = native_extension_schema_catalog()
    tasks = registered_model_tasks(controller)
    return {
        "version": 1,
        "model_tasks": tasks,
        "plugin_config": plugin_items,
        "native_extensions": extensions,
        "performance_tuning": performance_tuning_catalog(),
    }
