# -*- coding: utf-8 -*-
"""素材层：把图片/视频帧当作衣柜的原始资产来管理。

与语义层（wardrobe.py 的散件 / 整套）解耦：本模块只回答三个问题——
「有哪些原始文件」「它们从哪来」「它们是不是同一个文件」，
不理解衣物，也不参与提示词渲染。

设计约束：

1. 只用标准库 + 文件系统，不 import AstrBot，便于离线批处理与单测；
2. 以内容指纹（sha256）为唯一身份，重复导入不会产生第二条记录；
3. 同一份内容只存一份，多个语义记录可以引用同一个 asset_id。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ASSET_DIR_NAME = "wardrobe_assets"
ASSET_INDEX_NAME = "index.json"
ASSET_DRAFTS_DIR_NAME = "drafts"

ASSET_KIND_IMAGE = "image"
ASSET_KIND_VIDEO_FRAME = "video_frame"
ASSET_KIND_TEXT = "text"
ASSET_KINDS = (ASSET_KIND_IMAGE, ASSET_KIND_VIDEO_FRAME, ASSET_KIND_TEXT)

ASSET_ORIGIN_LOCAL = "local"
ASSET_ORIGIN_PANEL = "panel"
ASSET_ORIGIN_BLOGGER = "blogger"
ASSET_ORIGIN_TAOBAO = "taobao"
ASSET_ORIGIN_SHARE_TEXT = "share_text"
ASSET_ORIGIN_SCREENSHOT = "screenshot"
ASSET_ORIGINS = (
    ASSET_ORIGIN_LOCAL,
    ASSET_ORIGIN_PANEL,
    ASSET_ORIGIN_BLOGGER,
    ASSET_ORIGIN_TAOBAO,
    ASSET_ORIGIN_SHARE_TEXT,
    ASSET_ORIGIN_SCREENSHOT,
)

# 导入阶段的状态机：imported -> understood / rejected（理解层负责推进）
ASSET_STATUS_IMPORTED = "imported"
ASSET_STATUS_UNDERSTOOD = "understood"
ASSET_STATUS_REJECTED = "rejected"
ASSET_STATUSES = (ASSET_STATUS_IMPORTED, ASSET_STATUS_UNDERSTOOD, ASSET_STATUS_REJECTED)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"})

ASSET_MAX_SOURCE_PATH = 1200
ASSET_MAX_ORIGIN_NOTE = 200
ASSET_MAX_BYTES = 32 * 1024 * 1024
CHUNK = 1024 * 1024

# 只读小工具：PNG/JPEG 的宽高（不依赖 Pillow，插件运行时不一定装了它）
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Content fingerprint; the same bytes always yield the same id."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def asset_id_for(digest: str) -> str:
    return f"asset_{str(digest or '')[:16]}"


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())[:limit]


def normalize_asset_kind(value: Any, *, suffix: str = "") -> str:
    """Resolve one asset kind; fall back to the file suffix."""

    text = _clean_text(value, 32).casefold()
    if text in ASSET_KINDS:
        return text
    if text in {"img", "photo", "picture", "图片", "照片", "图"}:
        return ASSET_KIND_IMAGE
    if text in {"frame", "video", "视频", "视频帧", "抽帧"}:
        return ASSET_KIND_VIDEO_FRAME
    if text in {"txt", "text", "文本", "分享文本"}:
        return ASSET_KIND_TEXT
    suffix = str(suffix or "").casefold()
    if suffix in IMAGE_SUFFIXES:
        return ASSET_KIND_IMAGE
    if suffix in VIDEO_SUFFIXES:
        return ASSET_KIND_VIDEO_FRAME
    return ASSET_KIND_IMAGE


def normalize_asset_origin(value: Any) -> str:
    text = _clean_text(value, 32).casefold()
    if text in ASSET_ORIGINS:
        return text
    aliases = {
        "blogger": ASSET_ORIGIN_BLOGGER,
        "博主": ASSET_ORIGIN_BLOGGER,
        "参考": ASSET_ORIGIN_BLOGGER,
        "taobao": ASSET_ORIGIN_TAOBAO,
        "淘宝": ASSET_ORIGIN_TAOBAO,
        "天猫": ASSET_ORIGIN_TAOBAO,
        "share": ASSET_ORIGIN_SHARE_TEXT,
        "分享": ASSET_ORIGIN_SHARE_TEXT,
        "截图": ASSET_ORIGIN_SCREENSHOT,
        "screenshot": ASSET_ORIGIN_SCREENSHOT,
        "面板": ASSET_ORIGIN_PANEL,
        "local": ASSET_ORIGIN_LOCAL,
        "本地": ASSET_ORIGIN_LOCAL,
    }
    return aliases.get(text, ASSET_ORIGIN_LOCAL)


def normalize_asset_status(value: Any) -> str:
    text = _clean_text(value, 32).casefold()
    return text if text in ASSET_STATUSES else ASSET_STATUS_IMPORTED


def image_dimensions(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """Best-effort width/height for PNG and JPEG without extra dependencies."""

    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
            if head.startswith(_PNG_SIG):
                # 宽高在 IHDR 里，偏移 16 开始（读完签名要回退，别顺着往下读）
                handle.seek(16)
                data = handle.read(8)
                if len(data) >= 8:
                    width = int.from_bytes(data[0:4], "big")
                    height = int.from_bytes(data[4:8], "big")
                    if 0 < width <= 100000 and 0 < height <= 100000:
                        return width, height
                return None
            if head[:2] == b"\xff\xd8":
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    code = marker[1]
                    if code in {0xD8, 0xD9} or 0xD0 <= code <= 0xD7:
                        continue
                    size_raw = handle.read(2)
                    if len(size_raw) < 2:
                        return None
                    size = int.from_bytes(size_raw, "big")
                    if code in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                        body = handle.read(5)
                        if len(body) < 5:
                            return None
                        height = int.from_bytes(body[1:3], "big")
                        width = int.from_bytes(body[3:5], "big")
                        if width and height:
                            return width, height
                        return None
                    if size < 2:
                        return None
                    handle.seek(size - 2, os.SEEK_CUR)
    except (OSError, ValueError):
        return None
    return None


def asset_root(data_dir: str | os.PathLike[str]) -> Path:
    return Path(str(data_dir or ".")).expanduser() / ASSET_DIR_NAME


def asset_index_path(data_dir: str | os.PathLike[str]) -> Path:
    return asset_root(data_dir) / ASSET_INDEX_NAME


def asset_drafts_dir(data_dir: str | os.PathLike[str]) -> Path:
    return asset_root(data_dir) / ASSET_DRAFTS_DIR_NAME


def _safe_int(value: Any, default: int = 0) -> int:
    """把任意值折成非负整数；非法 / NaN / Inf 一律回落到 default。"""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0, int(number))


def normalize_asset(raw: Any) -> dict[str, Any] | None:
    """Normalize one stored asset row; unusable rows return None."""

    if not isinstance(raw, Mapping):
        return None
    digest = _clean_text(raw.get("sha256"), 64).casefold()
    if not digest:
        return None
    path = _clean_text(raw.get("path") or raw.get("file"), ASSET_MAX_SOURCE_PATH)
    suffix = Path(path).suffix
    created_at = raw.get("created_at")
    try:
        created_at = float(created_at)
    except (TypeError, ValueError):
        created_at = time.time()
    record = {
        "id": _clean_text(raw.get("id"), 80) or asset_id_for(digest),
        "kind": normalize_asset_kind(raw.get("kind"), suffix=suffix),
        "sha256": digest,
        "path": path,
        "origin": normalize_asset_origin(raw.get("origin")),
        "source_path": _clean_text(raw.get("source_path"), ASSET_MAX_SOURCE_PATH),
        "origin_note": _clean_text(raw.get("origin_note"), ASSET_MAX_ORIGIN_NOTE),
        "status": normalize_asset_status(raw.get("status")),
        # 与上面的 created_at 同规格：一行坏数据（例如手改出来的 "1.2MB"）
        # 不该让整份索引读不出来。
        "bytes": _safe_int(raw.get("bytes")),
        "width": _safe_int(raw.get("width")),
        "height": _safe_int(raw.get("height")),
        "created_at": created_at,
    }
    draft = raw.get("draft")
    if isinstance(draft, Mapping):
        record["draft"] = dict(draft)
    return record


def normalize_asset_index(value: Any) -> dict[str, dict[str, Any]]:
    """Normalize a whole index; keyed by asset id, duplicates by digest merged."""

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    # 索引文件是 {"version":…, "assets":[…]} 的包装结构；直接喂进来也要能解析，
    # 否则"读回来是空的"会让所有资产状态静默丢失（曾经真的踩过）。
    if isinstance(value, Mapping) and "assets" in value:
        value = value.get("assets")
    rows: Iterable[Any]
    if isinstance(value, Mapping):
        rows = value.values()
    elif isinstance(value, (list, tuple)):
        rows = value
    else:
        return {}
    index: dict[str, dict[str, Any]] = {}
    seen_digest: set[str] = set()
    for raw in rows:
        record = normalize_asset(raw)
        if record is None or record["sha256"] in seen_digest:
            continue
        seen_digest.add(record["sha256"])
        index[record["id"]] = record
    return index


def _quarantine_asset_index(path: Path) -> Path | None:
    """把读不出来的索引改名留档，避免下一次 save 把原始数据直接覆盖掉。"""

    try:
        backup = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        path.replace(backup)
        return backup
    except Exception:
        return None


def load_asset_index(data_dir: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    path = asset_index_path(data_dir)
    if not path.is_file():
        return {}
    try:
        # utf-8-sig：编辑器另存为「UTF-8 带 BOM」是很常见的一步，用 utf-8 读会直接
        # JSONDecodeError，整份索引被当成空 —— 下一次 save 就把它抹掉了。
        payload = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        _quarantine_asset_index(path)
        return {}
    if isinstance(payload, Mapping) and "assets" in payload:
        payload = payload.get("assets")
    return normalize_asset_index(payload)


def save_asset_index(data_dir: str | os.PathLike[str], index: Mapping[str, Any]) -> Path:
    path = asset_index_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "updated_at": time.time(),
        "assets": list(normalize_asset_index(index).values()),
    }
    # 原子替换：写一半被打断（进程被杀、磁盘满）不该留下半份索引 —— 半份索引在下次
    # load 时会被当成空，再 save 一次就把全部素材记录抹掉。
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


def find_asset_by_digest(
    index: Mapping[str, Mapping[str, Any]], digest: str
) -> dict[str, Any] | None:
    target = _clean_text(digest, 64).casefold()
    for record in index.values():
        if str(record.get("sha256") or "").casefold() == target:
            return dict(record)
    return None


def asset_abs_path(data_dir: str | os.PathLike[str], record: Mapping[str, Any]) -> Path:
    relative = _clean_text(record.get("path"), ASSET_MAX_SOURCE_PATH)
    return asset_root(data_dir) / relative if relative else asset_root(data_dir)


def scan_source_dir(
    source_dir: str | os.PathLike[str],
    *,
    recursive: bool = True,
    suffixes: Sequence[str] | None = None,
    limit: int = 0,
) -> list[Path]:
    """List candidate files in a source directory, sorted for stable output."""

    root = Path(str(source_dir or "")).expanduser()
    if not root.is_dir():
        return []
    allowed = {s.casefold() for s in (suffixes or ())} or (IMAGE_SUFFIXES | VIDEO_SUFFIXES)
    iterator = root.rglob("*") if recursive else root.glob("*")
    found: list[Path] = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.casefold() not in allowed:
            continue
        found.append(path)
    found.sort(key=lambda p: (p.parent.as_posix(), p.name))
    if limit and limit > 0:
        return found[:limit]
    return found


def import_asset(
    data_dir: str | os.PathLike[str],
    source: str | os.PathLike[str],
    *,
    index: Mapping[str, Mapping[str, Any]] | None = None,
    origin: str = ASSET_ORIGIN_LOCAL,
    kind: Any = "",
    origin_note: Any = "",
    move: bool = False,
    persist: bool = True,
    now: float | None = None,
) -> tuple[dict[str, Any], bool]:
    """Copy one file into the asset store; return (record, created).

    Idempotent: the same bytes map to the same asset_id and are copied once.
    """

    src = Path(str(source or "")).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在：{src}")
    size = src.stat().st_size
    if size > ASSET_MAX_BYTES:
        raise ValueError(f"文件超过 {ASSET_MAX_BYTES // (1024 * 1024)}MB：{src.name}")
    digest = sha256_file(src)
    # 没显式给索引时自己去读：这样"同一个文件导两次"天然幂等，
    # 调用方不必记得先 load_asset_index。
    known = dict(load_asset_index(data_dir) if index is None else index)
    existing = find_asset_by_digest(known, digest)
    if existing is not None:
        return existing, False
    asset_kind = normalize_asset_kind(kind, suffix=src.suffix)
    suffix = src.suffix.casefold() or ".bin"
    relative = f"{digest[:2]}/{digest}{suffix}"
    target = asset_root(data_dir) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        if move:
            shutil.move(str(src), str(target))
        else:
            shutil.copy2(src, str(target))
    width = height = 0
    if asset_kind != ASSET_KIND_TEXT:
        size_pair = image_dimensions(target)
        if size_pair:
            width, height = size_pair
    record = normalize_asset(
        {
            "id": asset_id_for(digest),
            "kind": asset_kind,
            "sha256": digest,
            "path": relative,
            "origin": origin,
            "source_path": str(src),
            "origin_note": origin_note,
            "status": ASSET_STATUS_IMPORTED,
            "bytes": size,
            "width": width,
            "height": height,
            "created_at": float(now if now is not None else time.time()),
        }
    )
    assert record is not None
    if persist:
        # 单文件导入默认自己落索引：否则文件复制进去了却不在索引里，
        # 下一次导入还会当成新资产（批量导入传 persist=False，末尾统一保存）。
        merged = dict(known)
        merged[record["id"]] = record
        save_asset_index(data_dir, merged)
    return record, True


def import_directory(
    data_dir: str | os.PathLike[str],
    source_dir: str | os.PathLike[str],
    *,
    origin: str = ASSET_ORIGIN_LOCAL,
    limit: int = 0,
    recursive: bool = True,
    on_progress: Callable[[int, int, Path], None] | None = None,
) -> dict[str, Any]:
    """Import a whole directory; returns counters and the updated index."""

    files = scan_source_dir(source_dir, recursive=recursive, limit=limit)
    index = load_asset_index(data_dir)
    stats = {"scanned": len(files), "imported": 0, "skipped": 0, "failed": 0, "errors": []}
    for position, path in enumerate(files, start=1):
        try:
            record, created = import_asset(
                data_dir, path, index=index, origin=origin, persist=False
            )
        except Exception as exc:  # 单个文件失败不该中断整批导入
            stats["failed"] += 1
            if len(stats["errors"]) < 20:
                stats["errors"].append(f"{path.name}: {exc}")
            continue
        index[record["id"]] = record
        stats["imported" if created else "skipped"] += 1
        if on_progress is not None:
            on_progress(position, len(files), path)
    save_asset_index(data_dir, index)
    stats["index"] = index
    return stats


def list_pending_drafts(data_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """List assets still waiting for confirmation, with whatever draft exists.

    草稿可能还没生成（只导入了图），所以 draft 字段允许为空——面板与 CLI
    都能据此区分"等识图"和"等确认"。
    """

    index = load_asset_index(data_dir)
    rows: list[dict[str, Any]] = []
    for asset_id, record in index.items():
        if str(record.get("status") or ASSET_STATUS_IMPORTED) != ASSET_STATUS_IMPORTED:
            continue
        draft = load_asset_draft(data_dir, asset_id) or {}
        rows.append(
            {
                "asset_id": asset_id,
                "origin": str(record.get("origin") or ""),
                "path": str(asset_abs_path(data_dir, record)),
                "width": int(record.get("width") or 0),
                "height": int(record.get("height") or 0),
                "has_draft": bool(draft),
                "kind": str(draft.get("kind") or ""),
                "name": str(draft.get("name") or ""),
                "description": str(draft.get("description") or ""),
                "slot": str(draft.get("slot") or ""),
                "tags": [str(tag) for tag in (draft.get("tags") or ())],
            }
        )
    rows.sort(key=lambda row: row["asset_id"])
    return rows


def mark_asset_status(
    data_dir: str | os.PathLike[str], asset_id: str, status: str
) -> dict[str, Any] | None:
    """Update one asset status in the index; returns the record or None."""

    index = load_asset_index(data_dir)
    record = index.get(str(asset_id or ""))
    if record is None:
        return None
    record["status"] = normalize_asset_status(status)
    index[record["id"]] = record
    save_asset_index(data_dir, index)
    return record


def write_asset_draft(
    data_dir: str | os.PathLike[str], asset_id: str, draft: Mapping[str, Any]
) -> Path:
    """Persist an understanding-layer draft next to the asset index."""

    directory = asset_drafts_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_clean_text(asset_id, 80) or 'asset'}.json"
    path.write_text(
        json.dumps({"asset_id": asset_id, "draft": dict(draft)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_asset_draft(data_dir: str | os.PathLike[str], asset_id: str) -> dict[str, Any] | None:
    path = asset_drafts_dir(data_dir) / f"{_clean_text(asset_id, 80) or 'asset'}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    draft = payload.get("draft") if isinstance(payload, Mapping) else None
    return dict(draft) if isinstance(draft, Mapping) else None


__all__ = [
    "ASSET_DIR_NAME",
    "ASSET_DRAFTS_DIR_NAME",
    "ASSET_INDEX_NAME",
    "ASSET_KINDS",
    "ASSET_KIND_IMAGE",
    "ASSET_KIND_TEXT",
    "ASSET_KIND_VIDEO_FRAME",
    "ASSET_MAX_BYTES",
    "ASSET_ORIGINS",
    "ASSET_ORIGIN_BLOGGER",
    "ASSET_ORIGIN_LOCAL",
    "ASSET_ORIGIN_PANEL",
    "ASSET_ORIGIN_SCREENSHOT",
    "ASSET_ORIGIN_SHARE_TEXT",
    "ASSET_ORIGIN_TAOBAO",
    "ASSET_STATUSES",
    "ASSET_STATUS_IMPORTED",
    "ASSET_STATUS_REJECTED",
    "ASSET_STATUS_UNDERSTOOD",
    "asset_abs_path",
    "asset_drafts_dir",
    "asset_id_for",
    "asset_index_path",
    "asset_root",
    "find_asset_by_digest",
    "image_dimensions",
    "import_asset",
    "import_directory",
    "list_pending_drafts",
    "mark_asset_status",
    "load_asset_draft",
    "load_asset_index",
    "normalize_asset",
    "normalize_asset_index",
    "normalize_asset_kind",
    "normalize_asset_origin",
    "normalize_asset_status",
    "save_asset_index",
    "scan_source_dir",
    "sha256_file",
    "write_asset_draft",
]
