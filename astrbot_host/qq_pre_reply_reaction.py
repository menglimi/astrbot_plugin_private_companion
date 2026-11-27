# -*- coding: utf-8 -*-
"""QQ 表情预回应（NapCat / OneBot v11）。

聊天被唤醒、确认要回复之后，在等待 LLM 生成回复的这段时间里，给**触发回复的
那条入站消息**贴一个「处理中 / 等待」表情；回复发出后（或超时兜底）撤销。

设计依据（AstrBot 4.29.3 实测）：
- ``OnWaitingLLMRequestEvent`` 的官方语义是「消息确定要调用 LLM、尚未排队等锁」，
  只在真正要回复时触发，天然满足「未命中回复的不需要」。
- 陪伴小屋在 ``main.py`` 的「交由 AstrBot 核心回复管线」之前设置
  ``event.is_wake = True``；所有不回复的分支都在更早处 return。因此命中判据是
  ``is_wake`` + 核心回复上下文存在。
- 动作走 NapCat OneBot v11 扩展 ``set_msg_emoji_like``，用适配器自带的
  ``event.bot.call_action``；本机已装的 astrbot_plugin_parser 也是这么调的。

本模块只做宿主侧适配，不 import companion_v2，也不 import 具体平台事件类：
任何提供 ``bot.call_action`` / ``message_obj`` / ``get_extra`` 的对象都能驱动，
便于脱离 AstrBot 进程做离线试运行。所有失败都吞掉并记日志，绝不影响正常回复。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

try:  # 在 AstrBot 内用宿主 logger；离线试运行时退回标准 logging
    from astrbot.api import logger
except Exception:  # pragma: no cover - 仅在独立试运行时走这里
    logger = logging.getLogger(__name__)


ACTION = "set_msg_emoji_like"
STATE_EXTRA = "_companion_v2_qq_pre_reply_reaction"
CORE_REPLY_CONTEXT_EXTRA_DEFAULT = "_companion_v2_core_reply_context"

CONFIG_KEY = "qq_pre_reply_reaction"

# 212=🤔 思考/处理中，324=👌 收到/等待，32=✅，351=👍，319=🎉，0=😲，14=🙂
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "emoji_id": 212,
    "emoji_type": "1",
    # 需求是「被唤醒、确定要回复的消息」就贴；陪伴小屋是否接管不是前置条件。
    # 真机实测：core_reply_context 在 OnWaitingLLMRequestEvent 时可能尚未写好
    # （陪伴小屋未接管的私聊尤其如此），默认 companion 会让功能几乎不生效。
    "scope": "all",
    "clear_after_reply": True,
    "timeout_seconds": 180,
}

# 等待 action 完成的最长时间。适配器自身的 api_timeout_sec=180，若不设上限，
# NapCat 挂起会让 after_message_sent 钩子一直卡住，所以这里一律 2 秒封顶。
_ACTION_TIMEOUT_SECONDS = 2.0
_APPLY_WAIT_SECONDS = 2.0

# 插件重载时用来取消还没结束的兜底任务；key 是 id(event)
_PENDING: dict[int, asyncio.Task] = {}


def read_settings(config: Any) -> dict[str, Any]:
    """从插件配置里读本功能的一段，缺省值兜底 + 归一化。"""
    raw: Any = {}
    if isinstance(config, Mapping):
        raw = config.get(CONFIG_KEY, {})
    if not isinstance(raw, Mapping):
        raw = {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update({key: raw[key] for key in DEFAULT_SETTINGS if key in raw})
    try:
        emoji_id = int(settings["emoji_id"])
    except (TypeError, ValueError):
        emoji_id = -1
    if emoji_id < 0:
        settings["enabled"] = False
    settings["emoji_id"] = emoji_id
    settings["enabled"] = bool(settings["enabled"])
    settings["clear_after_reply"] = bool(settings["clear_after_reply"])
    settings["emoji_type"] = str(settings["emoji_type"] or "").strip()
    settings["scope"] = "all" if str(settings["scope"]).strip() == "all" else "companion"
    try:
        timeout = float(settings["timeout_seconds"])
    except (TypeError, ValueError):
        timeout = float(DEFAULT_SETTINGS["timeout_seconds"])
    settings["timeout_seconds"] = max(5.0, min(timeout, 3600.0))
    return settings


def _is_hit(event: Any, *, core_extra_key: str, scope: str) -> bool:
    """只有「已唤醒、确定要回复」的消息才算命中。"""
    stopped = getattr(event, "is_stopped", None)
    if callable(stopped):
        try:
            if stopped():
                return False
        except Exception:
            pass
    if not bool(getattr(event, "is_wake", False)):
        return False
    if scope == "companion":
        # 只要求陪伴小屋在准入阶段挂过上下文；不要求 prepared 已经就绪——
        # OnWaitingLLMRequestEvent 早于 on_llm_request，部分分支此时还没写 prepared。
        state = event.get_extra(core_extra_key)
        if not isinstance(state, Mapping):
            return False
    return True


def _onebot_bot(event: Any) -> Any | None:
    """取 NapCat / OneBot v11 的 CQHttp 客户端；别的平台一律不碰。"""
    platform = ""
    getter = getattr(event, "get_platform_name", None)
    if callable(getter):
        try:
            platform = str(getter() or "").lower()
        except Exception:
            platform = ""
    # 能识别平台时严格一点；识别不到（离线 mock）时只看 bot 的形状。
    if platform and "aiocqhttp" not in platform and "onebot" not in platform:
        return None
    bot = getattr(event, "bot", None)
    if not callable(getattr(bot, "call_action", None)):
        return None
    return bot


def _inbound_message_id(event: Any) -> int | None:
    """入站消息的 OneBot message_id，优先用原始事件，再退回 AstrBotMessage。"""
    message_obj = getattr(event, "message_obj", None)
    raw = getattr(message_obj, "raw_message", None)
    candidates: list[Any] = []
    if isinstance(raw, Mapping):
        candidates.append(raw.get("message_id"))
    elif raw is not None:
        candidates.append(getattr(raw, "message_id", None))
    candidates.append(getattr(message_obj, "message_id", None))
    for value in candidates:
        if value is None or isinstance(value, bool):
            continue
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            return message_id
    return None


def _params(message_id: int, settings: Mapping[str, Any], *, value: bool) -> dict[str, Any]:
    params: dict[str, Any] = {
        "message_id": message_id,
        "emoji_id": settings["emoji_id"],
        "set": value,
    }
    # 官方最小参数只有上面三个；emoji_type 与本机 parser 插件的既有调用保持一致。
    if settings.get("emoji_type"):
        params["emoji_type"] = settings["emoji_type"]
    return params


async def _send_action(bot: Any, params: Mapping[str, Any]) -> bool:
    try:
        await asyncio.wait_for(
            bot.call_action(ACTION, **dict(params)),
            timeout=_ACTION_TIMEOUT_SECONDS)
        return True
    except Exception as error:  # 贴表情是纯附加反馈，失败绝不影响回复
        logger.warning("[CompanionV2] QQ 表情预回应动作失败，不影响回复 error_type=%s",
                       type(error).__name__)
        return False


async def apply_pre_reply_reaction(
    event: Any,
    *,
    config: Any,
    core_extra_key: str = CORE_REPLY_CONTEXT_EXTRA_DEFAULT,
) -> bool:
    """命中回复时贴「处理中」表情。返回是否安排了贴表情动作。

    真正的 OneBot 调用放在后台 task 里，避免在 LLM 请求前多一次网络往返。
    收尾时 ``clear_pre_reply_reaction`` 会等这个 task，保证不会「先撤后贴」。
    """
    try:
        settings = read_settings(config)
        if not settings["enabled"]:
            return False
        if not _is_hit(event, core_extra_key=core_extra_key, scope=settings["scope"]):
            return False
        if isinstance(event.get_extra(STATE_EXTRA), Mapping):
            return False  # 同一事件只贴一次（分段/多钩子）
        bot = _onebot_bot(event)
        if bot is None:
            return False
        message_id = _inbound_message_id(event)
        if message_id is None:
            return False
        params = _params(message_id, settings, value=True)
        entry: dict[str, Any] = {
            "bot": bot,
            "message_id": message_id,
            "settings": settings,
            "apply_task": None,
            "timeout_task": None,
        }
        event.set_extra(STATE_EXTRA, entry)
        loop = asyncio.get_running_loop()
        entry["apply_task"] = loop.create_task(_send_action(bot, params))
        entry["timeout_task"] = loop.create_task(
            _timeout_clear(event, settings["timeout_seconds"]))
        _PENDING[id(event)] = entry["timeout_task"]
        logger.info("[CompanionV2] QQ 表情预回应已贴 event_message_id=%s emoji_id=%s",
                    message_id, settings["emoji_id"])
        return True
    except Exception as error:
        logger.warning("[CompanionV2] QQ 表情预回应跳过，不影响回复 error_type=%s",
                       type(error).__name__)
        return False


async def _timeout_clear(event: Any, seconds: float) -> None:
    """回复始终没发出（模型失败 / 事件被丢弃）时，兜底把表情撤掉。"""
    try:
        await asyncio.sleep(seconds)
        await clear_pre_reply_reaction(event, reason="timeout")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("[CompanionV2] QQ 表情预回应超时兜底跳过", exc_info=True)


async def clear_pre_reply_reaction(event: Any, *, reason: str = "reply_sent") -> bool:
    """回复发出（或不回复/超时）时撤销表情；幂等、异常隔离。"""
    try:
        entry = event.get_extra(STATE_EXTRA)
        if not isinstance(entry, dict):
            return False
        event.set_extra(STATE_EXTRA, None)
        _PENDING.pop(id(event), None)

        apply_task = entry.get("apply_task")
        applied = False
        if apply_task is not None:
            if apply_task.done():
                applied = bool(apply_task.result()) if not apply_task.cancelled() else False
            else:
                try:
                    applied = bool(await asyncio.wait_for(
                        asyncio.shield(apply_task), timeout=_APPLY_WAIT_SECONDS))
                except Exception:
                    applied = False

        timeout_task = entry.get("timeout_task")
        current = asyncio.current_task()
        if timeout_task is not None and timeout_task is not current and not timeout_task.done():
            timeout_task.cancel()

        settings = entry["settings"]
        if not applied or not settings["clear_after_reply"]:
            return False
        params = _params(entry["message_id"], settings, value=False)
        ok = await _send_action(entry["bot"], params)
        if ok:
            logger.info("[CompanionV2] QQ 表情预回应已撤销 event_message_id=%s reason=%s",
                        entry["message_id"], reason)
        return ok
    except Exception as error:
        logger.warning("[CompanionV2] QQ 表情预回应撤销跳过，不影响回复 error_type=%s",
                       type(error).__name__)
        return False


async def cancel_all_pre_reply_reactions() -> int:
    """插件重载/卸载时取消全部兜底任务，不留孤儿协程。"""
    tasks = list(_PENDING.values())
    _PENDING.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    return len(tasks)
