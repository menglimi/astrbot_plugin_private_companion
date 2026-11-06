"""群聊文本化捕获丢掉的那一份视频位置。

群聊的核心回复路径走 ``capture_text_only``：它把每个 Video 组件折成一行
``[视频不可用]``（``companion_v2/adapters/astrbot/group.py:194-199``），原始位置
（``part.url or part.file``）在那一刻就不在返回值里了；真正采集位置的
``capture()``（``group.py:373-378``）不在这条路径上。而 AstrBot 的
``ProviderRequest`` 又没有 video 字段，所以位置一旦丢掉，就没有第二个地方能找回来。

群里"先发一条视频、再单独发一条 @"是两条独立消息。第二轮事件里没有任何 Video
组件，核心既写不出占位符、也扫不到视频，画面于是彻底消失。本模块在折成占位符
**之前**记下那一件事实——位置加定位句柄，永不保存字节——留给请求钩子按既有开关
决定要不要把它补挂进这一轮请求。

键是主机侧的五个标识 ``(platform_id, account_id, group_id, sender_id, role_id)``：

- 只按 ``group_id`` 会串群：同一个群号可能出现在两个平台/两个账号下，而同一个账号
  也可能带着多个群。
- 只按 ``unified_msg_origin`` 不稳：AstrBot 的群会话 id 同时存在 ``group`` 与
  ``sender_group`` 两种形态（``group.py:283-289``），同一个人换个会话模式就换了键。
- 五个标识一起才唯一确定"同一个人、在同一个群、在同一个人格窗口里"。换人格
  （``role_id`` 变）或换说话人（``sender_id`` 变）必然换键，所以永远不会把甲的
  视频挂到乙的请求里。

取值规则必须与 ``capture()`` 逐字一致（``group.py:374``）：
``file``（仅当它以 ``base64://`` 开头）→ ``url`` → ``file``。位置编号也照抄
``enumerate(parts)``，否则之后任何按位置比对原始媒体清单的地方都会拒收。
"""

from __future__ import annotations

from collections import OrderedDict
import time

from companion_v2.adapters.astrbot.native import NativeVideoLocator
from companion_v2.conversation.media.video.models import MAX_VIDEOS

#: 只在"下一轮很快到来"的语境里成立：一次完整回合加上工具轮足够，跨轮误用要挡住。
#: 私聊防抖最多 8 秒（``companion_v2/conversation/private_debounce.py:41``），群聊
#: 没有防抖，120 秒覆盖得了一次真实的"先发视频、再 @ 一句"。
TTL_SECONDS = 120.0
#: 有界上限，与既有的进程内表同一数量级（``astrbot_host/video/gemini_provider.py``
#: 的续接指纹表是 256 条）；超出时淘汰最久没用过的那条。
MAX_ENTRIES = 32

#: 可替换的时钟：测试直接注入时间点，不改系统时间也不影响别的用例。
_clock = time.monotonic

#: ``{五元组: (过期时刻, ((position, locator), ...))}``，只存位置与非敏感句柄。
_sources: OrderedDict[tuple, tuple[float, tuple[NativeVideoLocator, ...]]] = OrderedDict()


def source_key(*, platform_id, account_id, group_id, sender_id, role_id):
    """把五个标识拼成缓存键；缺任何一个都返回 None，让调用方安静跳过。

    返回元组而不是拼接字符串：拼接会让 ``("a", "bc")`` 与 ``("ab", "c")`` 撞在
    一起，而这里每一个字段都是主机侧的不透明标识。
    """
    values = (platform_id, account_id, group_id, sender_id, role_id)
    if any(type(value) is not str or not value.strip() for value in values):
        return None
    return tuple(value.strip() for value in values)


def key_from_capture(captured):
    """从已冻结的群输入快照取键（写入点用）。

    说话人取的是受众里的 ``human`` 收件人：``group_audience``（
    ``companion_v2/identity_policy/group_audience.py:8-17``）固定返回
    ``(speaker, group)``，而 ``GroupAuthority.select`` 会用
    ``captured.recipients != audience`` 逐字段校验（``astrbot_host/group_authority.py:39-41``），
    所以这里的 ``subject_id`` 与之后 ``TaskContext.actor.subject`` 里的那个必然相等。
    """
    try:
        scope = captured.route.scope
        recipients = getattr(getattr(captured, "recipients", None), "recipients", ())
        speaker = next((item for item in recipients
                        if getattr(getattr(item, "kind", None), "value", None) == "human"), None)
        return source_key(platform_id=getattr(scope, "platform", None),
                          account_id=getattr(scope, "account_id", None),
                          group_id=getattr(scope, "native_scope_id", None),
                          sender_id=getattr(speaker, "subject_id", None),
                          role_id=getattr(getattr(captured, "role", None), "subject_id", None))
    except Exception:  # noqa: BLE001 - 取不到键只表示"这一轮不记"
        return None


def key_from_context(context):
    """从这一轮请求的上下文取键（补挂点用），与 ``key_from_capture`` 一一对应。"""
    try:
        scope = context.scope
        if getattr(getattr(scope, "kind", None), "value", None) != "group":
            return None
        subject = getattr(getattr(context, "actor", None), "subject", None)
        role = getattr(getattr(context, "role_binding", None), "role", None)
        return source_key(platform_id=getattr(scope, "platform", None),
                          account_id=getattr(scope, "account_id", None),
                          group_id=getattr(scope, "native_scope_id", None),
                          sender_id=getattr(subject, "subject_id", None),
                          role_id=getattr(role, "subject_id", None))
    except Exception:  # noqa: BLE001
        return None


def video_positions(event, *, video_type, component_reader=None):
    """读这条消息里的视频位置，取值顺序与 ``capture()`` 完全一致。

    ``capture_text_only`` 只读文本骨架，从不触碰这里；位置是在它把组件折成
    ``[视频不可用]`` 之前取走的，所以这里必须能容忍任何异常：记不住位置不影响
    这一轮正常回复。条数上限直接用 ``MAX_VIDEOS``：连正常采集都不会接受更多，
    补挂自然也不需要。
    """
    if video_type is None:
        return ()
    try:
        parts = (component_reader(event) if component_reader is not None
                 else tuple(getattr(getattr(event, "message_obj", None), "message", ())))
    except Exception:  # noqa: BLE001
        return ()
    found = []
    for position, part in enumerate(parts if isinstance(parts, (list, tuple)) else ()):
        if type(part) is not video_type:
            continue
        if getattr(getattr(part, "type", None), "value", getattr(part, "type", None)) != "Video":
            continue
        file = getattr(part, "file", None)
        value = file if type(file) is str and file.startswith("base64://") else getattr(part, "url", None) or file
        if type(value) is str and value:
            found.append(NativeVideoLocator(position, value))
        if len(found) >= MAX_VIDEOS:
            break
    return tuple(found)


def remember(key, locators, *, at=None):
    """记下这一轮的位置，返回是否真的记了。空位置/空键都是安静的空操作。"""
    if not key or not locators:
        return False
    now = _clock() if at is None else at
    _prune(now)
    _sources[key] = (now + TTL_SECONDS, tuple(locators))
    _sources.move_to_end(key)
    while len(_sources) > MAX_ENTRIES:
        _sources.popitem(last=False)
    return True


def remember_capture(captured, locators, *, at=None):
    """写入点的便捷入口：从群输入快照取键再记。"""
    return remember(key_from_capture(captured), locators, at=at)


def take(key, *, at=None):
    """一次性取出并消费：同一条视频在下一轮不会被再挂一次。

    先弹出、再判过期，所以过期条目也会被顺手清掉，不会在表里僵着等 LRU。
    """
    captured = _sources.pop(key, None) if key else None
    if captured is None:
        return ()
    deadline, locators = captured
    now = _clock() if at is None else at
    return locators if now <= deadline else ()


def reset():
    """测试用：忘掉所有待补挂的位置。"""
    _sources.clear()


def _prune(now):
    """尽力清掉表头的过期条目。

    生产时钟单调，插入顺序就是过期顺序，所以从表头清最快；测试会注入非单调的
    时间点，那时这里可能漏掉一条，但正确性不靠它——``take`` 自己会判过期。
    """
    while _sources:
        key, (deadline, _locators) = next(iter(_sources.items()))
        if deadline >= now:
            return
        _sources.pop(key, None)
