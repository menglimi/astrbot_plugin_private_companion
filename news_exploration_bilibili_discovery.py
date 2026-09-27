# -*- coding: utf-8 -*-
"""NewsExplorationBilibiliDiscoveryMixin。

由 tools/split_mixin_domain.py 从 news_exploration.py 机械抽取（22 个方法 + 0 个模块级名字 + 0 个类级赋值 / 393 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 NewsExplorationMixin）。
"""
from __future__ import annotations

from .news_exploration_shared import BILIBILI_AI_BOT_LEGACY_DATA_NAMES, BILIBILI_AI_BOT_PLUGIN_NAME, logger
from .news_exploration_shared import Any
from .news_exploration_shared import Path
from .news_exploration_shared import _now_ts
from .news_exploration_shared import _safe_float
from .news_exploration_shared import _safe_int
from .news_exploration_shared import _single_line
from .news_exploration_shared import asyncio
from .news_exploration_shared import gc
from .news_exploration_shared import json
from .news_exploration_shared import re
from .news_exploration_shared import runtime_persona_setting



class NewsExplorationBilibiliDiscoveryMixin:
    """NewsExplorationBilibiliDiscoveryMixin（从 NewsExplorationMixin 拆出）。"""


    def _bilibili_plugin_dir(self) -> Path:
        candidates = self._bilibili_ai_bot_plugin_dirs()
        for path in candidates:
            if self._is_bilibili_ai_bot_dir(path):
                return path
        return Path(self.data_dir).parent.parent / "plugins" / BILIBILI_AI_BOT_PLUGIN_NAME

    def _bilibili_plugin_roots(self) -> list[Path]:
        return [
            Path(__file__).resolve().parent.parent,
            Path(self.data_dir).parent.parent / "plugins",
        ]

    def _is_bilibili_ai_bot_dir(self, path: Path) -> bool:
        if not (path / "main.py").exists():
            return False
        if path.name == BILIBILI_AI_BOT_PLUGIN_NAME:
            return True
        meta = path / "metadata.yaml"
        if not meta.exists():
            return False
        try:
            text = meta.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        return bool(
            re.search(r"(?m)^name:\s*astrbot_plugin_bilibili_ai_bot\b", text)
            or "github.com/chenluQwQ/astrbot_plugin_bilibili_ai_bot" in text
        )

    def _bilibili_ai_bot_plugin_dirs(self) -> list[Path]:
        # Startup-safe: do not glob all bilibili-like plugin folders here.
        # A broad scan can interact badly with AstrBot's plugin loading phase.
        found: list[Path] = []
        seen: set[str] = set()
        for root in self._bilibili_plugin_roots():
            for path in (root / BILIBILI_AI_BOT_PLUGIN_NAME,):
                key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                if self._is_bilibili_ai_bot_dir(path):
                    found.append(path)
        return found

    def _bilibili_ai_bot_package_names(self) -> list[str]:
        now = _now_ts()
        cache = getattr(self, "_bilibili_ai_bot_package_names_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 60:
            names = cache.get("names")
            if isinstance(names, list):
                return [str(name) for name in names if str(name or "").strip()]
        names = [BILIBILI_AI_BOT_PLUGIN_NAME]
        for path in self._bilibili_ai_bot_plugin_dirs():
            if path.name not in names:
                names.append(path.name)
        try:
            self._bilibili_ai_bot_package_names_cache = {"ts": now, "names": list(names)}
        except Exception:
            pass
        return names

    def _bilibili_ai_bot_data_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        plugin_data_root = Path(self.data_dir).parent.parent / "plugin_data"
        for plugin_name in BILIBILI_AI_BOT_LEGACY_DATA_NAMES:
            candidates.append(plugin_data_root / plugin_name)
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped

    def _bilibili_watch_log_file(self) -> Path:
        data_candidates = [path / "watch_log.json" for path in self._bilibili_ai_bot_data_dirs()]
        for path in data_candidates:
            if path.exists():
                return path
        return data_candidates[0] if data_candidates else self._bilibili_plugin_dir() / "watch_log.json"

    def _bilibili_available(self) -> bool:
        now = _now_ts()
        cache = getattr(self, "_bilibili_available_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return bool(cache.get("available"))
        try:
            available = bool(self._bilibili_ai_bot_plugin_dirs()) or self._bilibili_watch_log_file().exists()
        except Exception:
            available = False
        try:
            self._bilibili_available_cache = {"ts": now, "available": bool(available)}
        except Exception:
            pass
        return bool(available)

    def _find_bilibili_bot_instance(self) -> Any | None:
        now = _now_ts()
        cache = getattr(self, "_bilibili_bot_instance_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return cache.get("instance")
        names = tuple(self._bilibili_ai_bot_package_names())
        found = None
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)
            if callable(getter):
                for name in names:
                    obj = getter(name)
                    if obj is not None and (
                        callable(getattr(obj, "_run_proactive", None)) or hasattr(obj, "memory_api")
                    ):
                        found = obj
                        break
        except Exception:
            pass
        if found is None and names:
            for obj in gc.get_objects():
                try:
                    cls = obj.__class__
                    module = str(getattr(cls, "__module__", ""))
                    if not any(name in module for name in names):
                        continue
                    if (callable(getattr(obj, "_run_proactive", None)) and hasattr(obj, "_proactive_task")) or hasattr(obj, "memory_api"):
                        found = obj
                        break
                except Exception:
                    continue
        try:
            self._bilibili_bot_instance_cache = {"ts": now, "instance": found}
        except Exception:
            pass
        return found

    def _find_bilibili_memory_api(self) -> Any | None:
        now = _now_ts()
        cache = getattr(self, "_bilibili_memory_api_object_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            return cache.get("api")
        bili = self._find_bilibili_bot_instance()
        api = getattr(bili, "memory_api", None) if bili is not None else None
        if api is not None and callable(getattr(api, "get_recent_memories", None)):
            try:
                self._bilibili_memory_api_object_cache = {"ts": now, "api": api}
            except Exception:
                pass
            return api
        names = tuple(self._bilibili_ai_bot_package_names())
        for obj in gc.get_objects():
            try:
                cls = obj.__class__
                module = str(getattr(cls, "__module__", ""))
                if not any(name in module for name in names):
                    continue
                api = getattr(obj, "memory_api", None)
                if api is not None and callable(getattr(api, "get_recent_memories", None)):
                    try:
                        self._bilibili_memory_api_object_cache = {"ts": now, "api": api}
                    except Exception:
                        pass
                    return api
            except Exception:
                continue
        try:
            self._bilibili_memory_api_object_cache = {"ts": now, "api": None}
        except Exception:
            pass
        return None

    def _bilibili_memory_api_available(self, *, allow_probe: bool = True, ttl_seconds: int = 60) -> bool:
        now = _now_ts()
        cache = getattr(self, "_bilibili_memory_api_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < max(5, ttl_seconds):
            return bool(cache.get("available"))
        if not allow_probe:
            return False
        available = self._find_bilibili_memory_api() is not None
        try:
            self._bilibili_memory_api_cache = {"ts": now, "available": bool(available)}
        except Exception:
            pass
        return bool(available)

    def _find_bilibili_runtime_objects(self) -> list[Any]:
        now = _now_ts()
        cache = getattr(self, "_bilibili_runtime_objects_cache", None)
        if isinstance(cache, dict) and now - _safe_float(cache.get("ts"), 0) < 30:
            objects = cache.get("objects")
            if isinstance(objects, list):
                return list(objects)
        names = tuple(self._bilibili_ai_bot_package_names())
        found: list[Any] = []
        seen: set[int] = set()
        if names:
            for obj in gc.get_objects():
                try:
                    cls = obj.__class__
                    module = str(getattr(cls, "__module__", ""))
                    if not any(name in module for name in names):
                        continue
                    if id(obj) in seen:
                        continue
                    if (
                        callable(getattr(obj, "get_video_info", None))
                        or callable(getattr(getattr(obj, "bili_client", None), "get_video_info", None))
                        or callable(getattr(obj, "_http_get", None))
                    ):
                        seen.add(id(obj))
                        found.append(obj)
                except Exception:
                    continue
        try:
            self._bilibili_runtime_objects_cache = {"ts": now, "objects": list(found)}
        except Exception:
            pass
        return found

    def _load_bilibili_watch_log(self) -> list[dict[str, Any]]:
        try:
            path = self._bilibili_watch_log_file()
            if not path.exists():
                return []
            with path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
        except Exception as e:
            logger.debug(f"读取 B 站观看日志失败: {e}")
        return []

    def _load_bilibili_recent_video_memories(self, *, hours: int = 72, limit: int = 12) -> list[dict[str, Any]]:
        api = self._find_bilibili_memory_api()
        if api is None:
            return []
        try:
            memories = api.get_recent_memories(
                source="bilibili",
                memory_types={"video"},
                hours=hours,
                limit=limit,
            )
            if isinstance(memories, list):
                return [item for item in memories if isinstance(item, dict)]
        except Exception as e:
            logger.debug(f"读取 BiliBot 视频记忆失败: {e}")
        return []

    def _bilibili_memory_bvid(self, item: dict[str, Any]) -> str:
        bvid = self._clean_bilibili_share_field(item.get("bvid") or item.get("video_bvid"), 40)
        if bvid:
            return bvid
        text = str(item.get("text") or "")
        checker = getattr(self, "_looks_like_internal_provider_error_text", None)
        if callable(checker) and checker(text):
            return ""
        match = re.search(r"\bBV[0-9A-Za-z]{8,16}\b", text)
        return _single_line(match.group(0), 40) if match else ""

    def _bilibili_memory_title(self, item: dict[str, Any]) -> str:
        title = self._clean_bilibili_share_field(item.get("video_title") or item.get("title"), 80)
        if title:
            return title
        text = str(item.get("text") or "")
        checker = getattr(self, "_looks_like_internal_provider_error_text", None)
        if callable(checker) and checker(text):
            return ""
        match = re.search(r"《([^》]{1,80})》", text)
        return self._clean_bilibili_share_field(match.group(1), 80) if match else ""

    def _bilibili_memory_context_for_bvid(self, bvid: str, *, limit: int = 3) -> list[str]:
        safe_bvid = _single_line(bvid, 40)
        if not safe_bvid:
            return []
        contexts: list[str] = []
        for item in self._load_bilibili_recent_video_memories(limit=18):
            if self._bilibili_memory_bvid(item) != safe_bvid:
                continue
            text = self._clean_bilibili_share_field(item.get("text"), 180)
            if text and text not in contexts:
                contexts.append(text)
            if len(contexts) >= limit:
                break
        return contexts

    def _bilibili_video_candidate_from_memory(self) -> dict[str, Any] | None:
        candidates = self._bilibili_video_candidates_from_memory(limit=1)
        return candidates[0] if candidates else None

    def _bilibili_video_candidates_from_memory(self, *, limit: int = 6) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in self._load_bilibili_recent_video_memories(limit=max(1, limit * 2)):
            bvid = self._bilibili_memory_bvid(item)
            title = self._bilibili_memory_title(item)
            if not bvid or not title:
                continue
            text = self._clean_bilibili_share_field(item.get("text"), 220)
            candidates.append({
                "key": f"{bvid}:{_single_line(item.get('time'), 20)}:memory",
                "bvid": bvid,
                "title": title,
                "up_name": "",
                "score": runtime_persona_setting(self, "bilibili_share_min_score", 7),
                "mood": "",
                "comment": text,
                "review": text,
                "pic": "",
                "time": _single_line(item.get("time"), 24),
                "actions": [],
                "source": "memory_api",
                "memory_context": [text] if text else [],
            })
            if len(candidates) >= limit:
                break
        return candidates

    def _latest_bilibili_video_candidates(self, *, include_memory_api: bool = True, limit: int = 8) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        logs = self._load_bilibili_watch_log()
        if logs:
            for item in reversed(logs[-40:]):
                bvid = self._clean_bilibili_share_field(item.get("bvid"), 32)
                title = self._clean_bilibili_share_field(item.get("title"), 80)
                if not bvid or not title:
                    continue
                score = _safe_int(item.get("score"), 0, 0, 10)
                if score < runtime_persona_setting(self, "bilibili_share_min_score", 7):
                    continue
                key = f"{bvid}:{_single_line(item.get('time'), 20)}"
                if key in seen:
                    continue
                seen.add(key)
                comment = self._clean_bilibili_share_field(item.get("comment"), 120)
                review = self._clean_bilibili_share_field(item.get("review"), 180)
                candidates.append({
                    "key": key,
                    "bvid": bvid,
                    "title": title,
                    "up_name": self._clean_bilibili_share_field(item.get("up_name"), 40),
                    "score": score,
                    "mood": self._clean_bilibili_share_field(item.get("mood"), 24),
                    "comment": comment,
                    "review": review,
                    "pic": _single_line(item.get("pic"), 240),
                    "time": _single_line(item.get("time"), 24),
                    "actions": list(item.get("actions") or []) if isinstance(item.get("actions"), list) else [],
                    "source": "watch_log",
                    "memory_context": self._bilibili_memory_context_for_bvid(bvid) if include_memory_api else [],
                })
                if len(candidates) >= limit:
                    break
        if include_memory_api and len(candidates) < limit:
            for item in self._bilibili_video_candidates_from_memory(limit=limit - len(candidates)):
                key = str(item.get("key") or item.get("bvid") or "")
                if key and key not in seen:
                    seen.add(key)
                    candidates.append(item)
        return candidates[:limit]

    def _latest_bilibili_video_candidate(self, *, include_memory_api: bool = True) -> dict[str, Any] | None:
        candidates = self._latest_bilibili_video_candidates(include_memory_api=include_memory_api, limit=1)
        return candidates[0] if candidates else None

    def _record_bilibili_share_to_memory(self, user_id: str, candidate: dict[str, Any]) -> None:
        api = self._find_bilibili_memory_api()
        if api is None or not callable(getattr(api, "record", None)):
            return
        bvid = self._clean_bilibili_share_field(candidate.get("bvid"), 40)
        title = self._clean_bilibili_share_field(candidate.get("title"), 80)
        if not bvid or not title:
            return
        async def _record() -> None:
            try:
                await api.record(
                    f"陪伴插件准备把视频《{title}》轻轻分享给 QQ 用户 {user_id}，链接 {bvid}",
                    user_id=str(user_id),
                    username="PrivateCompanion",
                    source="private_companion",
                    memory_type="video",
                    level="today",
                    importance=4,
                    extra={"bvid": bvid, "video_title": title},
                )
            except Exception as e:
                logger.debug(f"写入 BiliBot 分享记忆失败: {e}")
        operation = _record()
        creator = getattr(self, "_create_lifecycle_background_task", None)
        try:
            if callable(creator):
                task = creator(operation, label="bilibili_share_memory")
                if task is None:
                    close = getattr(operation, "close", None)
                    if callable(close):
                        close()
            else:
                task = asyncio.create_task(operation, name="private-companion-bilibili-share-memory")

                def consume(done_task: asyncio.Task) -> None:
                    try:
                        done_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        logger.warning(
                            "B站分享记忆后台任务失败: %s",
                            _single_line(exc, 160),
                        )

                task.add_done_callback(consume)
        except RuntimeError:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
