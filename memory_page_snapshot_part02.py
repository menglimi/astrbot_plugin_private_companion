# -*- coding: utf-8 -*-
"""MemoryPageSnapshotServicePart02Mixin。

由 tools/split_mixin_domain.py 从 memory_page_snapshot.py 机械抽取（15 个方法 + 0 个模块级名字 + 0 个类级赋值 / 461 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 MemoryPageSnapshotService）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import stat
try:  # package import
    from .memory_page_snapshot_shared import (
        MEMORY_PAGE_PHOTO_MAX_BYTES,
        MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES,
        MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
        _DETAIL_STATUSES,
        _PHOTO_REF_RE,
        _date_text,
        _event_list,
        _text,
        _timestamp,
    )
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import (
        MEMORY_PAGE_PHOTO_MAX_BYTES,
        MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES,
        MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
        _DETAIL_STATUSES,
        _PHOTO_REF_RE,
        _date_text,
        _event_list,
        _text,
        _timestamp,
    )
from pathlib import Path
from typing import Any
try:  # package import
    from .memory_page_snapshot_shared import MemoryPageSnapshotError
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import MemoryPageSnapshotError
try:  # package import
    from .memory_page_snapshot_shared import _PhotoBlob
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _PhotoBlob
try:  # package import
    from .memory_page_snapshot_shared import _PhotoRegistration
except ImportError:  # direct test/import from the plugin directory
    from memory_page_snapshot_shared import _PhotoRegistration



class MemoryPageSnapshotServicePart02Mixin:
    """MemoryPageSnapshotServicePart02Mixin（从 MemoryPageSnapshotService 拆出）。"""


    def _project_details(
        self,
        data: dict[str, Any],
        selected_date: str,
        generation: str,
    ) -> list[dict[str, Any]]:
        segments: dict[Any, Any] = {}
        if _date_text(data.get("detail_enhanced_day")) == selected_date:
            raw = data.get("detail_enhanced_segments")
            if isinstance(raw, dict):
                segments = raw
        else:
            history = data.get("detail_enhanced_history")
            if isinstance(history, list):
                for item in reversed(history[-512:]):
                    if not isinstance(item, dict) or _date_text(item.get("date")) != selected_date:
                        continue
                    raw = item.get("segments")
                    if isinstance(raw, dict):
                        segments = raw
                    break

        result: list[dict[str, Any]] = []
        for position, (raw_key, snapshot) in enumerate(segments.items()):
            if position >= 64:
                break
            if not isinstance(snapshot, dict):
                continue
            key = raw_key if isinstance(raw_key, str) else ""
            match = re.fullmatch(r"\d{4}-\d{2}-\d{2}:(\d{1,3}):(\d{1,2}:\d{2})", key)
            raw_index = snapshot.get("index")
            index = (
                raw_index
                if isinstance(raw_index, int)
                and not isinstance(raw_index, bool)
                and 0 <= raw_index < 18
                else None
            )
            if index is None and match:
                parsed_index = int(match.group(1))
                index = parsed_index if parsed_index < 18 else None
            raw_status = _text(snapshot.get("status"), 24).lower()
            status_map = {
                "done": "ready",
                "complete": "ready",
                "completed": "ready",
                "ready": "ready",
                "planned": "planned",
                "pending": "planned",
                "generating": "planned",
                "observed": "observed",
                "failed": "degraded",
                "degraded": "degraded",
            }
            status = status_map.get(raw_status, raw_status if raw_status in _DETAIL_STATUSES else "unknown")
            time_text = _text(snapshot.get("time") or snapshot.get("start_time"), 20)
            if not time_text and match:
                time_text = match.group(2)
            identity = f"detail|{generation}|{selected_date}|{key}|{position}"
            result.append(
                {
                    "id": f"detail_{self._opaque(identity)}",
                    "index": index,
                    "status": status,
                    "time": time_text,
                    "summary": _text(snapshot.get("summary"), 180),
                    "today_events": _event_list(snapshot.get("today_events")),
                    "proactive_events": _event_list(snapshot.get("proactive_events")),
                    "state_variables": _event_list(snapshot.get("state_variables")),
                }
            )
            if len(result) >= 18:
                return result

        story = self._story_plan_for_date(data, selected_date)
        if story and len(result) < 18:
            today_events = _event_list(story.get("today_events"))
            proactive_events = _event_list(story.get("proactive_events"))
            summary = _text(story.get("summary"), 180)
            if summary or today_events or proactive_events:
                result.append(
                    {
                        "id": f"detail_{self._opaque(f'story|{generation}|{selected_date}')}",
                        "index": None,
                        "status": "story_plan",
                        "time": "",
                        "summary": summary,
                        "today_events": today_events,
                        "proactive_events": proactive_events,
                        "state_variables": [],
                    }
                )
        return result

    @staticmethod
    def _story_plan_for_date(data: dict[str, Any], selected_date: str) -> dict[str, Any]:
        current = data.get("daily_story_plan")
        if isinstance(current, dict) and _date_text(current.get("date")) == selected_date:
            return current
        history = data.get("daily_story_plan_history")
        if isinstance(history, list):
            for item in reversed(history[-512:]):
                if isinstance(item, dict) and _date_text(item.get("date")) == selected_date:
                    return item
        return {}

    def _project_diaries(self, data: dict[str, Any], selected_date: str) -> list[dict[str, Any]]:
        raw = data.get("bot_diaries")
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in reversed(raw[-512:]):
            if not isinstance(item, dict) or _date_text(item.get("date")) != selected_date:
                continue
            story = item.get("story_plan") if isinstance(item.get("story_plan"), dict) else {}
            tags = item.get("tags")
            safe_tags: list[str] = []
            if isinstance(tags, list):
                for tag in tags[:32]:
                    value = _text(tag, 40)
                    if value and value not in safe_tags:
                        safe_tags.append(value)
                    if len(safe_tags) >= 8:
                        break
            result.append(
                {
                    "date": selected_date,
                    "summary": _text(item.get("summary"), 220),
                    "body": _text(item.get("body"), 520),
                    "share_seed": _text(item.get("share_seed"), 180),
                    "tags": safe_tags,
                    "today_events": _event_list(item.get("today_events") or story.get("today_events")),
                    "proactive_events": _event_list(
                        item.get("proactive_events") or story.get("proactive_events")
                    ),
                    "long_term_events": _event_list(
                        item.get("long_term_events") or story.get("long_term_events")
                    ),
                }
            )
            if len(result) >= 4:
                break
        return result

    def _photo_candidates(
        self,
        data: dict[str, Any],
        selected_date: str,
        generation: str,
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[str, dict[str, Any]]] = []
        current = data.get("daily_outfit_photo")
        if isinstance(current, dict):
            candidates.append(("daily_outfit", current))
        history = data.get("daily_outfit_history")
        if isinstance(history, list):
            candidates.extend(
                ("daily_outfit", item)
                for item in reversed(history[-128:])
                if isinstance(item, dict)
            )
        recent = data.get("recent_photo_generations")
        if isinstance(recent, list):
            candidates.extend(
                ("recent_photo", item)
                for item in recent[:256]
                if isinstance(item, dict)
            )

        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for position, (default_kind, item) in enumerate(candidates):
            generated_at = _timestamp(item.get("generated_at") or item.get("ts"))
            date_key = _date_text(item.get("date")) or self._date_from_timestamp(generated_at)
            if date_key != selected_date:
                continue
            raw_path = item.get("path")
            path_identity = os.fspath(raw_path) if isinstance(raw_path, (str, os.PathLike)) else ""
            raw_kind = _text(item.get("kind"), 40).lower()
            kind = default_kind
            if default_kind == "recent_photo" and "life" in raw_kind:
                kind = "life_photo"
            identity_key = (kind, date_key, path_identity)
            if identity_key in seen:
                continue
            seen.add(identity_key)
            opaque_identity = f"photo|{generation}|{date_key}|{kind}|{position}|{path_identity}"
            result.append(
                {
                    "id": f"photo_{self._opaque(opaque_identity)}",
                    "date": date_key,
                    "kind": kind,
                    "generated_at": generated_at,
                    "_raw_path": raw_path,
                }
            )
        result.sort(key=lambda item: item["generated_at"], reverse=True)
        return result[:8]

    def _prepare_photos_sync(
        self,
        plugin: Any,
        candidates: list[dict[str, Any]],
        generation: str,
    ) -> tuple[list[dict[str, Any]], list[_PhotoBlob | None]]:
        roots = self._trusted_roots(plugin)
        rows: list[dict[str, Any]] = []
        registrations: list[_PhotoBlob | None] = []
        for candidate in candidates[:8]:
            raw_path = candidate.get("_raw_path")
            row = {
                "id": candidate["id"],
                "date": candidate["date"],
                "kind": candidate["kind"],
                "generated_at": candidate["generated_at"],
                "available": False,
                "error_code": "memory_page_photo_unavailable",
                "photo_ref": "",
            }
            blob: _PhotoBlob | None = None
            try:
                root, parts = self._authorize_photo_path(raw_path, roots)
                blob = self._read_authorized_photo(root, parts)
                row["available"] = True
                row["error_code"] = ""
            except MemoryPageSnapshotError as error:
                row["error_code"] = error.code
            rows.append(row)
            registrations.append(blob)
        return rows, registrations

    def _stage_photo_refs(
        self,
        rows: list[dict[str, Any]],
        blobs: list[_PhotoBlob | None],
        generation: str,
    ) -> tuple[list[dict[str, Any]], list[tuple[str, _PhotoBlob]]]:
        self._require_ready(generation)
        staged: list[tuple[str, _PhotoBlob]] = []
        for row, blob in zip(rows, blobs):
            if blob is None:
                continue
            identity = "|".join(
                (
                    generation,
                    str(blob.device),
                    str(blob.inode),
                    str(blob.size),
                    str(blob.mtime_ns),
                    blob.sha256,
                )
            )
            photo_ref = f"mphoto_{generation[:12]}_{self._opaque(identity)}"
            row["photo_ref"] = photo_ref
            staged.append((photo_ref, blob))
        self._require_ready(generation)
        return rows, staged

    def _commit_photo_refs(
        self,
        staged: list[tuple[str, _PhotoBlob]],
        generation: str,
    ) -> None:
        self._require_ready(generation)
        now = self._clock()
        with self._photo_refs_lock:
            previous = self._photo_refs.copy()
            try:
                self._require_ready(generation)
                self._prune_refs_locked(now)
                for photo_ref, blob in staged:
                    self._photo_refs[photo_ref] = _PhotoRegistration(
                        generation=generation,
                        root=blob.root,
                        parts=blob.parts,
                        device=blob.device,
                        inode=blob.inode,
                        size=blob.size,
                        mtime_ns=blob.mtime_ns,
                        mime_type=blob.mime_type,
                        sha256=blob.sha256,
                        expires_at=now + MEMORY_PAGE_PHOTO_REF_TTL_SECONDS,
                    )
                    self._photo_refs.move_to_end(photo_ref)
                while len(self._photo_refs) > MEMORY_PAGE_PHOTO_REF_MAX_ENTRIES:
                    self._photo_refs.popitem(last=False)
                self._require_ready(generation)
            except BaseException:
                self._photo_refs.clear()
                self._photo_refs.update(previous)
                raise

    def _lookup_photo_ref(self, photo_ref: str, generation: str) -> _PhotoRegistration:
        now = self._clock()
        with self._photo_refs_lock:
            self._require_ready(generation)
            registration = self._photo_refs.get(photo_ref)
            if registration is None:
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            if registration.generation != generation:
                raise MemoryPageSnapshotError("memory_page_photo_ref_stale")
            if registration.expires_at <= now:
                self._photo_refs.pop(photo_ref, None)
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            self._prune_refs_locked(now, preserve=photo_ref)
            self._photo_refs.move_to_end(photo_ref)
            return registration

    def _recheck_photo_ref(
        self,
        photo_ref: str,
        registration: _PhotoRegistration,
        generation: str,
    ) -> None:
        now = self._clock()
        with self._photo_refs_lock:
            self._require_ready(generation)
            current = self._photo_refs.get(photo_ref)
            if current is not registration:
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")
            if registration.expires_at <= now:
                self._photo_refs.pop(photo_ref, None)
                self._prune_refs_locked(now)
                raise MemoryPageSnapshotError("memory_page_photo_ref_expired")

    def _prune_refs_locked(self, now: float, *, preserve: str = "") -> None:
        for key, item in list(self._photo_refs.items()):
            if key != preserve and item.expires_at <= now:
                self._photo_refs.pop(key, None)

    @staticmethod
    def _validate_photo_ref(photo_ref: Any, generation: str) -> str:
        if not isinstance(photo_ref, str):
            raise MemoryPageSnapshotError("memory_page_photo_ref_invalid")
        match = _PHOTO_REF_RE.fullmatch(photo_ref)
        if not match:
            raise MemoryPageSnapshotError("memory_page_photo_ref_invalid")
        if match.group(1) != generation[:12]:
            raise MemoryPageSnapshotError("memory_page_photo_ref_stale")
        return photo_ref

    def _opaque(self, identity: str) -> str:
        digest = hmac.new(self._secret, identity.encode("utf-8"), hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _trusted_roots(plugin: Any) -> tuple[Path, ...]:
        candidates: list[Any] = [
            getattr(plugin, "data_dir", None),
            getattr(plugin, "plugin_data_dir", None),
        ]
        data_file = getattr(plugin, "data_file", None)
        if isinstance(data_file, (str, os.PathLike)):
            candidates.append(Path(data_file).parent)
        roots: list[Path] = []
        for raw in candidates:
            if not isinstance(raw, (str, os.PathLike)):
                continue
            try:
                root = Path(raw).resolve(strict=True)
                if not root.is_dir() or root in roots:
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            roots.append(root)
        return tuple(roots)

    @staticmethod
    def _authorize_photo_path(raw_path: Any, roots: tuple[Path, ...]) -> tuple[Path, tuple[str, ...]]:
        if not isinstance(raw_path, (str, os.PathLike)):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        try:
            value = os.fspath(raw_path)
        except TypeError:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable") from None
        if not isinstance(value, str) or not value or "\x00" in value:
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        separators = [os.sep]
        if os.altsep:
            separators.append(os.altsep)
        raw_parts = [value]
        for separator in separators:
            raw_parts = [piece for part in raw_parts for piece in part.split(separator)]
        if any(part in {".", ".."} for part in raw_parts):
            raise MemoryPageSnapshotError("memory_page_photo_unavailable")
        candidate = Path(value)
        for root in roots:
            try:
                lexical = Path(os.path.abspath(candidate if candidate.is_absolute() else root / candidate))
                relative = lexical.relative_to(root)
            except (OSError, ValueError):
                continue
            parts = relative.parts
            if not parts or len(parts) > 32 or any(
                not part or part in {".", ".."} or len(part.encode("utf-8")) > 255
                for part in parts
            ):
                continue
            return root, tuple(parts)
        raise MemoryPageSnapshotError("memory_page_photo_unavailable")

    @classmethod
    def _read_authorized_photo(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        expected: _PhotoRegistration | None = None,
    ) -> _PhotoBlob:
        fd = cls._open_nofollow(root, parts)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_unavailable"
                raise MemoryPageSnapshotError(code)
            if expected is not None and (
                before.st_dev != expected.device
                or before.st_ino != expected.inode
                or before.st_size != expected.size
                or before.st_mtime_ns != expected.mtime_ns
            ):
                raise MemoryPageSnapshotError("memory_page_photo_changed")
            if before.st_size > MEMORY_PAGE_PHOTO_MAX_BYTES:
                raise MemoryPageSnapshotError("memory_page_photo_too_large")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(1024 * 1024, MEMORY_PAGE_PHOTO_MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MEMORY_PAGE_PHOTO_MAX_BYTES:
                    raise MemoryPageSnapshotError("memory_page_photo_too_large")
            after = os.fstat(fd)
        except MemoryPageSnapshotError:
            raise
        except OSError:
            code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_read_failed"
            raise MemoryPageSnapshotError(code) from None
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or total != after.st_size
        ):
            raise MemoryPageSnapshotError("memory_page_photo_changed")
        content = b"".join(chunks)
        mime_type = cls._detect_image_mime(content)
        if not mime_type:
            code = "memory_page_photo_changed" if expected is not None else "memory_page_photo_unsupported"
            raise MemoryPageSnapshotError(code)
        digest = hashlib.sha256(content).hexdigest()
        if expected is not None and (mime_type != expected.mime_type or digest != expected.sha256):
            raise MemoryPageSnapshotError("memory_page_photo_changed")
        return _PhotoBlob(
            root=root,
            parts=parts,
            device=after.st_dev,
            inode=after.st_ino,
            size=total,
            mtime_ns=after.st_mtime_ns,
            mime_type=mime_type,
            sha256=digest,
            content=content,
        )
