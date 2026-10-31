"""把 V2 自己完成的角色记录交给当前对外提供记忆的一方。

## 为什么需要它

V2 与「我会牢牢记住你」（下称牢插件）同处一个进程时，V2 的记忆**表达**让位给
牢插件（见 :mod:`companion_v2.capabilities.ownership`），可是记录的**产生**仍然发生
在 V2 自己的领域里：日记、梦境、主动消息都只落进 V2 的库。结果是角色写了一篇
日记、做了一个梦、主动说了一句话，牢插件的记忆一无所知——用户在那边问起「你
昨天写了什么」，它答不出来。

这个端口就是那条单向链路：领域动作**完成之后**，把这条记录推给对外表达记忆的
一方。它只写不读，也不要回执。

## 三条纪律

1. **推送是旁路，不是动作的一部分**。日记提交、主动消息入队之后才推；牢插件不在、
   被停用、句柄坏掉、写库抛异常，领域动作照样算成功（见 :func:`push_memory`）。
2. **身份由 V2 自己给**。每条记录带一个从 V2 持久身份推导的 ``memory_id``
   （见 :func:`push_memory_id`）。牢插件的 ``record_event`` 没有幂等键，没有这个
   id 时它会按来源+会话+正文自行推导：同一记录重复推送就成了两条记忆，而两条
   不同记录又可能撞车。所以这个 id 必须稳定、且只由记录自身的身份决定。
3. **不缓冲、不重试**。对方不在就当场放弃：V2 的库才是原始记录，补写属于维护
   工作，不该伪装成正常推送路径，也不该在内存里越积越多。
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

from companion_v2.execution.models import TaskContext, digest


logger = logging.getLogger(__name__)

PUSH_PERSONA_LIFE = "persona_life"
"""角色自己的私人生活（日记、梦境）。"""

PUSH_PROACTIVE_MESSAGE = "proactive_message"
"""角色自己发起的主动消息。"""

PUSH_KINDS = (PUSH_PERSONA_LIFE, PUSH_PROACTIVE_MESSAGE)

MEMORY_PUSH_ID_PREFIX = "companion_v2"
"""id 前缀：让接收方一眼看出这条记忆的产地，也避免与别家 id 撞名。"""

MEMORY_ID_LIMIT = 120
"""接收方对 ``memory_id`` 的截断上限；超长的 id 会被截短后失去唯一性。"""


@dataclass(frozen=True)
class MemoryPushRecord:
    """一条已经完成的角色记录：内容由领域写，身份由 :func:`push_memory_id` 给。

    ``metadata`` 用不可变的键值对元组而不是字典：它跨领域传递，谁也不该在推送
    途中改掉别人写下的字段。值可以是字符串、数字或列表（接收方按 JSON 存放）。
    """

    memory_id: str
    content: str
    kind: str = PUSH_PERSONA_LIFE
    scope: str = "unknown"
    session_id: str = ""
    platform: str = ""
    occurred_at: str = ""
    tags: tuple[str, ...] = ()
    metadata: tuple[tuple[str, object], ...] = ()
    confidence: float | None = None
    importance: float | None = None
    context: TaskContext | None = None

    def __post_init__(self):
        if self.context is not None and not isinstance(self.context, TaskContext):
            raise ValueError("memory push context must be a resolved task context")
        if self.kind not in PUSH_KINDS:
            raise ValueError("memory push needs a known record kind")
        if (not isinstance(self.memory_id, str) or not self.memory_id.strip()
                or len(self.memory_id) > MEMORY_ID_LIMIT):
            raise ValueError("memory push requires a bounded deterministic memory identity")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("memory push requires recallable content")
        if not isinstance(self.scope, str) or not isinstance(self.session_id, str) or not isinstance(self.platform, str):
            raise ValueError("memory push routing fields must be strings")
        if not isinstance(self.occurred_at, str):
            raise ValueError("memory push time must be an ISO string")
        if not isinstance(self.tags, tuple) or any(not isinstance(tag, str) or not tag for tag in self.tags):
            raise ValueError("memory push tags must be immutable non-empty strings")
        if (not isinstance(self.metadata, tuple)
                or any(not isinstance(item, tuple) or len(item) != 2
                       or not isinstance(item[0], str) or not item[0] for item in self.metadata)):
            raise ValueError("memory push metadata must be immutable key/value pairs")
        for field in (self.confidence, self.importance):
            if field is not None and (isinstance(field, bool) or not isinstance(field, (int, float))):
                raise ValueError("memory push weights must be numbers")

    def document(self) -> dict:
        """给日志和测试用的纯 JSON 文档；不参与接收方的写入形状。"""

        return {"memory_id": self.memory_id, "kind": self.kind, "content": self.content,
                "scope": self.scope, "session_id": self.session_id, "platform": self.platform,
                "occurred_at": self.occurred_at, "tags": list(self.tags),
                "metadata": dict(self.metadata), "confidence": self.confidence,
                "importance": self.importance}


def push_memory_id(kind: str, *identity) -> str:
    """从 V2 自己的持久身份推导稳定 id：同一记录重复推送只会有同一条记忆。

    ``identity`` 只能是这条记录在 V2 里的持久身份（日记是键+修订号，主动消息是
    contact_id）。**不要**掺进时间戳、随机数或运行时序号：那正是让「同一记录推送
    两次」变成两条记忆的原因。内容同样不进身份——一次改写是另一条记录，由修订号
    区分，正文不该参与身份判定。
    """

    if kind not in PUSH_KINDS:
        raise ValueError("memory push identity needs a known record kind")
    if not identity:
        raise ValueError("memory push identity requires the record's own durable identity")
    return MEMORY_PUSH_ID_PREFIX + "_" + kind + "_" + digest([MEMORY_PUSH_ID_PREFIX, kind, *identity])


class MemoryPushPort(Protocol):
    """宿主提供的窄端口：把一条已完成的记录推给当前对外表达记忆的一方。

    实现必须自己判断对方在不在、句柄活不活，并且**允许失败**：判定失败、对方不在
    或者写入抛异常，都由 :func:`push_memory` 吞掉，绝不抛给调用方的领域动作。
    端口因此不回答「推送成不成功」，只回答「对方给的记忆 id」（没有就是 ``None``）。
    """

    async def push(self, record: MemoryPushRecord) -> str | None:
        """Push one completed role record to the current memory owner."""


class NullMemoryPush:
    """没有接线时的默认端口：什么都不做，也永远不会失败。"""

    async def push(self, record: MemoryPushRecord) -> str | None:
        return None


DEFAULT_MEMORY_PUSH = NullMemoryPush()


async def push_memory(port, record: MemoryPushRecord) -> str | None:
    """安全的推送入口：推送的任何失败都不影响已经完成的领域动作。

    这是调用方唯一该用的方式。日记已经提交、主动消息已经入队，此时牢插件不在、
    被停用、句柄坏掉或者写库抛异常，都只能记一条日志然后继续——反过来让一次旁路
    推送掀掉一次已经落地的书写，是这条链路唯一不可接受的后果。
    """

    if port is None:
        return None
    try:
        result = await port.push(record)
    except Exception as error:  # noqa: BLE001 —— 见上：任何失败都止步于此，不外抛
        logger.debug("对外记忆推送失败（已忽略）：%s",
                     getattr(error, "code", None) or type(error).__name__)
        return None
    return result if isinstance(result, str) and result else None


__all__ = [
    "DEFAULT_MEMORY_PUSH",
    "MEMORY_ID_LIMIT",
    "MEMORY_PUSH_ID_PREFIX",
    "MemoryPushPort",
    "MemoryPushRecord",
    "NullMemoryPush",
    "PUSH_KINDS",
    "PUSH_PERSONA_LIFE",
    "PUSH_PROACTIVE_MESSAGE",
    "push_memory",
    "push_memory_id",
]
