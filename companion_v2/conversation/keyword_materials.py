"""关键词触发、带约束的材料注入。

一条材料只有在三种条件同时成立时才会被注入：

1. 登记时就声明了关键词，且**当前这条用户消息**里出现了其中之一；
2. 材料自己判断对当前窗口可用（通常是私聊 + 已配置来源 + 权限）；
3. 取数成功，或取数失败但能给出原因。

这样普通消息不会为任何来源多打一次请求，也不会出现"模型手里没数据却开始猜"。
失败同样作为材料注入（``state=unavailable``），让回复能说"这次没取到"，而不是
换个说法假装看到了。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any

MAX_MATERIALS = 3
MAX_MATERIAL_CHARS = 2048
FAILURE_TTL_SECONDS = 60
DEFAULT_TTL_SECONDS = 300


@dataclass(frozen=True)
class KeywordMaterialSpec:
    """一条「关键词 → 材料」的登记项。"""

    owner: str
    title: str
    instruction: str
    keywords: tuple[str, ...]
    prepare: Callable[[Any, str, datetime], Awaitable[Mapping[str, Any] | None]]
    available: Callable[[Any], bool] | None = None
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    max_chars: int = MAX_MATERIAL_CHARS

    def __post_init__(self):
        if not isinstance(self.owner, str) or not self.owner.strip() or len(self.owner) > 120:
            raise ValueError("keyword material needs a bounded owner")
        if not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 40:
            raise ValueError("keyword material needs a short title")
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise ValueError("keyword material needs an instruction")
        words = tuple(word.strip() for word in self.keywords if isinstance(word, str) and word.strip())
        if not words:
            raise ValueError("keyword material needs at least one keyword")
        if any(len(word) > 24 for word in words):
            raise ValueError("keyword material keywords must stay short")
        if not callable(self.prepare):
            raise ValueError("keyword material needs a prepare callable")
        if type(self.ttl_seconds) is not int or not 0 <= self.ttl_seconds <= 86400:
            raise ValueError("keyword material ttl must be a non-negative duration")

    def usable(self, context) -> bool:
        """来源是否对当前窗口可用；判定失败一律当作不可用。"""
        if self.available is None:
            return True
        try:
            return bool(self.available(context))
        except Exception:
            return False

    def matches(self, text) -> bool:
        if type(text) is not str or not text:
            return False
        folded = text.casefold()
        return any(word.casefold() in folded for word in self.keywords)


class KeywordMaterials:
    """登记表 + 每次回复前的一次性取数。"""

    def __init__(self, specs: tuple[KeywordMaterialSpec, ...] = ()):
        owners = [spec.owner for spec in specs]
        if len(set(owners)) != len(owners):
            raise ValueError("keyword material owners must be unique")
        self.specs = tuple(specs)
        self._cache: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
        self._generations: dict[str, int] = {}

    def invalidate(self, owner: str) -> None:
        """Discard one source, including results from its in-flight fetches."""
        self._cache.pop(owner, None)
        self._generations[owner] = self._generations.get(owner, 0) + 1

    def matches(self, context, text, at: datetime = None) -> tuple[KeywordMaterialSpec, ...]:
        chosen = [spec for spec in self.specs if spec.usable(context) and spec.matches(text)]
        return tuple(chosen[:MAX_MATERIALS])

    def _fresh(self, spec: KeywordMaterialSpec, at: datetime):
        cached = self._cache.get(spec.owner)
        if cached is None:
            return None
        expires_at, payload = cached
        return payload if at is None or at < expires_at else None

    async def prepare(self, context, text, at: datetime = None) -> tuple[dict[str, Any], ...]:
        """按当前消息取一次材料；单条失败不影响其它材料。"""
        entries = []
        for spec in self.matches(context, text, at):
            payload = self._fresh(spec, at)
            if payload is None:
                generation = self._generations.get(spec.owner, 0)
                try:
                    payload = await spec.prepare(context, text, at)
                except Exception as error:  # noqa: BLE001 - 失败要变成可读材料，不能打断回复
                    payload = {"state": "unavailable",
                               "code": getattr(error, "code", type(error).__name__)}
                if payload is None:
                    payload = {"state": "unavailable", "code": "material_unavailable"}
                failed = isinstance(payload, Mapping) and payload.get("state") == "unavailable"
                # ttl_seconds=0 表示这条材料不进缓存（内容取决于本轮消息，缓存会串轮次）。
                if (at is not None and spec.ttl_seconds > 0
                        and self._generations.get(spec.owner, 0) == generation):
                    ttl = FAILURE_TTL_SECONDS if failed else spec.ttl_seconds
                    self._cache[spec.owner] = (at + timedelta(seconds=ttl), payload)
            if not isinstance(payload, Mapping):
                payload = {"state": "unavailable", "code": "material_invalid"}
            entries.append({"owner": spec.owner, "title": spec.title,
                            "instruction": spec.instruction, "payload": dict(payload)})
        return tuple(entries)


def render(entries) -> str:
    """把取到的材料渲染成提示词里的一段；空列表返回空串。"""
    blocks = []
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            continue
        body = json.dumps(entry.get("payload"), ensure_ascii=False, sort_keys=True, default=str)
        if len(body) > MAX_MATERIAL_CHARS:
            body = body[:MAX_MATERIAL_CHARS]
        blocks.append("【" + str(entry.get("title") or "材料") + "】\n"
                      + str(entry.get("instruction") or "") + "\n" + body)
    return "\n\n".join(blocks)
