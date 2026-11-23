"""
Bilibili live danmaku client.

This is a small, self-contained WebSocket client inspired by Super Agent Party's
live router design: connect to Bilibili live danmaku, normalize events, and let
the plugin decide how to consume them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import hashlib
import hmac
import json
import re
import struct
import time
import uuid
import urllib.parse
import zlib
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from astrbot.api import logger

try:
    from .blivedm import WebClient as BlivedmWebClient
    from .blivedm.models import message as blivedm_message
except Exception:
    BlivedmWebClient = None
    blivedm_message = None

try:
    import brotli
except Exception:
    brotli = None


HEADER_LEN = 16
OP_HEARTBEAT = 2
OP_HEARTBEAT_REPLY = 3
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8

OPEN_LIVE_START_URL = "https://live-open.biliapi.com/v2/app/start"
OPEN_LIVE_HEARTBEAT_URL = "https://live-open.biliapi.com/v2/app/heartbeat"
OPEN_LIVE_END_URL = "https://live-open.biliapi.com/v2/app/end"
BILI_LIVE_AREA_LIST_URL = "https://api.live.bilibili.com/room/v1/Area/getList"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Origin": "https://live.bilibili.com",
    "Referer": "https://live.bilibili.com/",
}
WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
]


@dataclasses.dataclass
class LiveDanmakuEvent:
    event_type: str
    username: str
    content: str
    ts: float = dataclasses.field(default_factory=time.time)
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)
    user_id: str = ""
    event_id: str = ""
    amount: float | int | None = None
    dm_type: int = 0
    emoticon: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def is_emoticon_danmaku(self) -> bool:
        return self.event_type == "danmaku" and self.dm_type == 1

    @property
    def is_voice_danmaku(self) -> bool:
        return self.event_type == "danmaku" and self.dm_type == 2

    def __post_init__(self) -> None:
        self.dm_type = _safe_int(self.dm_type, 0)
        self.emoticon = _normalize_emoticon_options(self.emoticon)
        if not self.user_id:
            self.user_id = _extract_bilibili_user_id(self.raw)
        if self.amount is None:
            self.amount = _extract_bilibili_amount(self.raw)
        if not self.event_id:
            self.event_id = _build_live_event_id(self)

    def display_text(self) -> str:
        if self.event_type == "danmaku":
            return f"{self.username}: {self.content}"
        return f"{self.username} {self.content}".strip()


def _extract_bilibili_user_id(raw: dict[str, Any]) -> str:
    if not isinstance(raw, dict):
        return ""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    user_info = data.get("user_info") if isinstance(data.get("user_info"), dict) else {}
    candidates = [
        raw.get("uid"), raw.get("user_id"), raw.get("userId"), raw.get("mid"),
        data.get("uid"), data.get("user_id"), data.get("userId"), data.get("mid"),
        user_info.get("uid"), user_info.get("user_id"), user_info.get("mid"),
    ]
    info = raw.get("info")
    if isinstance(info, list) and len(info) > 2 and isinstance(info[2], (list, tuple)) and info[2]:
        candidates.append(info[2][0])
    for value in candidates:
        text = str(value or "").strip()
        if text and text != "0":
            return text
    return ""


def _extract_bilibili_amount(raw: dict[str, Any]) -> float | int | None:
    if not isinstance(raw, dict):
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    for value in (data.get("price"), data.get("rmb"), raw.get("priceNormalized"), raw.get("price"), raw.get("rmb")):
        if value in (None, ""):
            continue
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except (TypeError, ValueError):
            continue
    return None


def _build_live_event_id(event: LiveDanmakuEvent) -> str:
    raw = event.raw if isinstance(event.raw, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    for value in (
        raw.get("id_str"), raw.get("id"), raw.get("messageId"), raw.get("msg_id"), raw.get("rnd"),
        data.get("id_str"), data.get("message_id"), data.get("msg_id"), data.get("rnd"),
    ):
        text = str(value or "").strip()
        if text:
            return f"bili_live:{text}"
    payload = {
        "type": event.event_type,
        "uid": event.user_id,
        "name": event.username,
        "content": event.content,
        "ts": None if raw else round(float(event.ts), 3),
        "raw": raw,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return "bili_live:" + hashlib.sha256(encoded).hexdigest()[:24]


@dataclasses.dataclass(frozen=True)
class BilibiliLiveArea:
    part_id: int
    part_name: str
    area_id: int
    area_name: str
    pinyin: str = ""
    locked: bool = False

    def display_text(self) -> str:
        suffix = "（可能受限）" if self.locked else ""
        return (
            f"{self.part_name} / {self.area_name}"
            f"（part_id={self.part_id}, area_id={self.area_id}）{suffix}"
        )


class BilibiliLiveError(Exception):
    pass


class BilibiliLiveRiskControlError(BilibiliLiveError):
    pass


async def fetch_bilibili_live_areas() -> list[BilibiliLiveArea]:
    async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        async with session.get(
            BILI_LIVE_AREA_LIST_URL,
            params={"show_pinyin": 1},
            timeout=10,
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)

    if payload.get("code") != 0:
        raise BilibiliLiveError(
            f"直播分区列表获取失败: code={payload.get('code')} message={payload.get('message')}"
        )

    areas: list[BilibiliLiveArea] = []
    for part in payload.get("data") or []:
        if not isinstance(part, dict):
            continue
        part_id = _safe_int(part.get("id"))
        part_name = str(part.get("name") or "").strip()
        for area in part.get("list") or []:
            if not isinstance(area, dict):
                continue
            area_id = _safe_int(area.get("id"))
            area_name = str(area.get("name") or "").strip()
            if not part_id or not area_id or not area_name:
                continue
            parent_id = _safe_int(area.get("parent_id")) or part_id
            areas.append(
                BilibiliLiveArea(
                    part_id=parent_id,
                    part_name=part_name,
                    area_id=area_id,
                    area_name=area_name,
                    pinyin=str(area.get("pinyin") or "").strip(),
                    locked=str(area.get("lock_status") or "").strip() == "1",
                )
            )
    return areas


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_emoticon_options(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


class BilibiliBlivedmClient:
    """Web live client backed by the vendored astrbot_plugin_bilibili_live blivedm."""

    def __init__(
        self,
        room_id: int,
        on_event: Callable[[LiveDanmakuEvent], Awaitable[None]],
        sessdata: str = "",
        debug_log: bool = False,
    ):
        self.room_id = int(room_id)
        self.sessdata = sessdata.strip()
        self.cookie_str = normalize_cookie_string(self.sessdata)
        self.on_event = on_event
        self.debug_log = debug_log
        self.real_room_id: Optional[int] = None
        self._client = None
        self._stop_event = asyncio.Event()
        self.last_error: str = ""

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    async def run_forever(self) -> None:
        if BlivedmWebClient is None or blivedm_message is None:
            raise BilibiliLiveError("blivedm 后端不可用，请确认 blivedm 目录完整。")

        self._stop_event.clear()
        self.last_error = ""
        self._client = BlivedmWebClient(self.room_id, cookie_str=self.cookie_str)
        self._client.start()
        logger.info(f"[B站直播] 已使用 blivedm Web 后端启动监听，房间={self.room_id}")
        try:
            while not self._stop_event.is_set():
                message_task = asyncio.create_task(self._client.get_message())
                network_task = getattr(self._client, "_network_future", None)
                wait_tasks = {message_task}
                if network_task:
                    wait_tasks.add(network_task)

                try:
                    done, pending = await asyncio.wait(
                        wait_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except BaseException:
                    if not message_task.done():
                        message_task.cancel()
                    try:
                        await message_task
                    except BaseException:
                        pass
                    raise

                if network_task and network_task in done:
                    if not message_task.done():
                        message_task.cancel()
                    try:
                        await message_task
                    except BaseException:
                        pass
                    if self._stop_event.is_set():
                        break
                    if network_task.cancelled():
                        break
                    exc = network_task.exception()
                    if exc:
                        self.last_error = str(exc)
                        raise BilibiliLiveError(f"blivedm 网络协程已结束: {exc}")
                    raise BilibiliLiveError("blivedm 网络协程已结束")

                cancelled_pending = []
                for task in pending:
                    if task is not network_task:
                        task.cancel()
                        cancelled_pending.append(task)
                if cancelled_pending:
                    await asyncio.gather(*cancelled_pending, return_exceptions=True)

                message = message_task.result()
                if self._stop_event.is_set():
                    break
                self.real_room_id = getattr(message, "room_id", None) or self.real_room_id
                event = self._message_to_event(message)
                if event:
                    await self.on_event(event)
                elif self.debug_log:
                    logger.info(
                        f"[B站直播调试] blivedm 未映射消息: {type(message).__name__}"
                    )
        except Exception as e:
            self.last_error = str(e)
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stop_event.set()
        client = self._client
        self._client = None
        if client:
            try:
                await client.stop_and_close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"[B站直播] blivedm 停止异常: {e}")

    def _message_to_event(self, message) -> Optional[LiveDanmakuEvent]:
        if blivedm_message is None:
            return None

        raw = getattr(message, "raw_data", None) or {}
        username = str(getattr(message, "user_name", "") or "观众")
        user_id = str(getattr(message, "user_id", "") or "")

        if isinstance(message, blivedm_message.DanmakuMessage):
            return LiveDanmakuEvent(
                "danmaku",
                username,
                str(getattr(message, "content", "") or ""),
                raw=raw,
                user_id=user_id,
                dm_type=_safe_int(getattr(message, "dm_type", 0)),
                emoticon=_normalize_emoticon_options(
                    getattr(message, "emoticon", None)
                    or getattr(message, "emoticon_options", "")
                ),
            )

        if isinstance(message, blivedm_message.GiftMessage):
            gift_name = str(getattr(message, "gift_name", "") or "礼物")
            gift_num = getattr(message, "gift_num", None) or 1
            return LiveDanmakuEvent(
                "gift", username, f"赠送 {gift_name} x{gift_num}", raw=raw, user_id=user_id
            )

        if isinstance(message, blivedm_message.SuperChatMessage):
            content = str(getattr(message, "message", "") or "")
            price = getattr(message, "price", None)
            prefix = f"发送醒目留言 {price}元" if price else "发送醒目留言"
            return LiveDanmakuEvent(
                "super_chat", username, f"{prefix}: {content}", raw=raw, user_id=user_id, amount=price
            )

        if isinstance(message, blivedm_message.LikeMessage):
            like_text = str(getattr(message, "like_text", "") or "点赞了直播间")
            like_count = getattr(message, "like_count", None)
            suffix = f" x{like_count}" if like_count else ""
            return LiveDanmakuEvent("like", username, f"{like_text}{suffix}", raw=raw, user_id=user_id)

        if isinstance(message, blivedm_message.EnterRoomMessage):
            return LiveDanmakuEvent("enter_room", username, "进入直播间", raw=raw, user_id=user_id)

        if isinstance(message, blivedm_message.GuardBuyMessage):
            level_names = {1: "总督", 2: "提督", 3: "舰长"}
            level = level_names.get(getattr(message, "guard_level", 0), "大航海")
            num = getattr(message, "guard_num", None) or 1
            unit = str(getattr(message, "guard_unit", "") or "月")
            return LiveDanmakuEvent(
                "buy_guard", username, f"成为了{level} x{num}{unit}", raw=raw, user_id=user_id
            )

        return None


def encode_wbi_params(params: dict[str, Any], mixin_key: str) -> dict[str, Any]:
    signed = dict(params)
    signed["wts"] = int(time.time())
    signed = {
        key: "".join(char for char in str(value) if char not in "!'()*")
        for key, value in signed.items()
    }
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


def get_mixin_key(img_key: str, sub_key: str) -> str:
    raw_key = img_key + sub_key
    if len(raw_key) < 64:
        raise BilibiliLiveError("Wbi 原始 key 长度不足")
    return "".join(raw_key[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]


async def fetch_wbi_mixin_key(session: aiohttp.ClientSession) -> str:
    async with session.get(
        "https://api.bilibili.com/x/web-interface/nav",
        params={},
        timeout=10,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    wbi_img = (data.get("data") or {}).get("wbi_img") or {}
    img_url = str(wbi_img.get("img_url") or "")
    sub_url = str(wbi_img.get("sub_url") or "")
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    return get_mixin_key(img_key, sub_key)


class BilibiliLiveClient:
    def __init__(
        self,
        room_id: int,
        on_event: Callable[[LiveDanmakuEvent], Awaitable[None]],
        sessdata: str = "",
        reconnect_interval: float = 5.0,
        debug_log: bool = False,
        history_poll_interval: float = 3.0,
        websocket_enabled: bool = True,
    ):
        self.room_id = int(room_id)
        self.sessdata = sessdata.strip()
        self.on_event = on_event
        self.reconnect_interval = reconnect_interval
        self.debug_log = debug_log
        self.history_poll_interval = max(0.0, float(history_poll_interval or 0.0))
        self.websocket_enabled = bool(websocket_enabled)
        self.real_room_id: Optional[int] = None

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._stop_event = asyncio.Event()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._history_task: Optional[asyncio.Task] = None
        self._history_seen_ids: set[str] = set()
        self._history_initialized = False
        self._history_started_at = 0.0
        self._wbi_mixin_key: Optional[str] = None
        self._warned_missing_brotli = False

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    async def run_forever(self) -> None:
        self._stop_event.clear()
        self._session = aiohttp.ClientSession(
            headers=HTTP_HEADERS,
            cookies=parse_cookie_string(self.sessdata),
        )
        try:
            self.real_room_id = await self._resolve_room_id()
            self._history_started_at = time.time()
            self._start_history_polling()
            if not self.websocket_enabled:
                logger.info(
                    "[B站直播] 已启动 history-only 弹幕监听: room=%s interval=%.1fs",
                    self.real_room_id,
                    self.history_poll_interval,
                )
                while not self._stop_event.is_set():
                    await asyncio.sleep(1.0)
                return
            retry_delay = self.reconnect_interval
            while not self._stop_event.is_set():
                try:
                    await self._run_once()
                    retry_delay = self.reconnect_interval
                except asyncio.CancelledError:
                    raise
                except BilibiliLiveRiskControlError as e:
                    if self._stop_event.is_set():
                        break
                    logger.warning(
                        "[B站直播] 弹幕接口疑似被 B站风控，%.0fs 后重试: %s",
                        retry_delay,
                        e,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(max(retry_delay * 2, 30.0), 300.0)
                except Exception as e:
                    if self._stop_event.is_set():
                        break
                    logger.warning(f"[B站直播] 监听连接异常，准备重连: {e}")
                    await asyncio.sleep(self.reconnect_interval)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except BaseException:
                    pass
                self._heartbeat_task = None
            if self._history_task:
                self._history_task.cancel()
                try:
                    await self._history_task
                except BaseException:
                    pass
                self._history_task = None
            ws = self._ws
            self._ws = None
            if ws and not ws.closed:
                try:
                    await ws.close()
                except Exception as exc:
                    logger.debug(f"[B站直播] 关闭 WebSocket 异常: {exc}")
        finally:
            session = self._session
            self._session = None
            if session and not session.closed:
                await session.close()

    async def _resolve_room_id(self) -> int:
        data = await self._get_json(
            "https://api.live.bilibili.com/room/v1/Room/room_init",
            params={"id": self.room_id},
        )
        if data.get("code") != 0:
            raise BilibiliLiveError(f"房间信息获取失败: {data}")
        room_data = data.get("data") or {}
        if room_data.get("live_status") == 0:
            logger.info(f"[B站直播] 房间 {self.room_id} 当前未开播，仍会尝试监听弹幕")
        return int(room_data.get("room_id") or self.room_id)

    async def _get_danmu_info(self) -> dict[str, Any]:
        params = await self._sign_wbi_params(
            {"id": self.real_room_id or self.room_id, "type": 0}
        )
        data = await self._get_json(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
            params=params,
        )
        if data.get("code") != 0:
            if int(data.get("code") or 0) == -352:
                hint = "请在直播插件配置中填写浏览器 B站 Cookie/SESSDATA，或暂停几分钟后再启动监听"
                if self.sessdata:
                    hint = "当前 Cookie 仍被风控或已失效，请更新 B站 Cookie/SESSDATA，或暂停几分钟后再启动监听"
                raise BilibiliLiveRiskControlError(
                    f"弹幕服务器信息获取失败: code=-352 message={data.get('message')}；{hint}"
                )
            raise BilibiliLiveError(f"弹幕服务器信息获取失败: {data}")
        return data.get("data") or {}

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        async with self._session.get(url, params=params, timeout=10) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _sign_wbi_params(self, params: dict[str, Any]) -> dict[str, Any]:
        mixin_key = await self._get_wbi_mixin_key()
        return encode_wbi_params(params, mixin_key)

    async def _get_wbi_mixin_key(self) -> str:
        if self._wbi_mixin_key:
            return self._wbi_mixin_key
        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        self._wbi_mixin_key = await fetch_wbi_mixin_key(self._session)
        return self._wbi_mixin_key

    def _start_history_polling(self) -> None:
        if self.history_poll_interval <= 0:
            return
        if self._history_task and not self._history_task.done():
            return
        self._history_task = asyncio.create_task(self._history_poll_loop())

    async def _history_poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                events = await self._fetch_history_events()
                if not self._history_initialized:
                    self._history_initialized = True
                    if self.debug_log:
                        logger.info(
                            f"[B站直播调试] 历史弹幕轮询已初始化，记录 {len(self._history_seen_ids)} 条已有弹幕"
                        )
                else:
                    if events:
                        logger.info(f"[B站直播] 历史弹幕轮询捕获 {len(events)} 条新弹幕")
                    for event in events:
                        await self.on_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"[B站直播] 历史弹幕轮询失败: {e}")
            await asyncio.sleep(self.history_poll_interval)

    async def _fetch_history_events(self) -> list[LiveDanmakuEvent]:
        data = await self._get_json(
            "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory",
            params={
                "roomid": self.real_room_id or self.room_id,
                "room_type": 0,
                "_": int(time.time() * 1000),
            },
        )
        if data.get("code") != 0:
            raise BilibiliLiveError(f"历史弹幕获取失败: {data}")

        rows = (data.get("data") or {}).get("room") or []
        events: list[LiveDanmakuEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_id = self._history_event_id(row)
            if event_id in self._history_seen_ids:
                continue
            self._history_seen_ids.add(event_id)
            event = self._history_row_to_event(row)
            if event and (
                self._history_initialized
                or (self._history_started_at and event.ts >= self._history_started_at - 1)
            ):
                events.append(event)

        if len(self._history_seen_ids) > 2000:
            self._history_seen_ids = set(list(self._history_seen_ids)[-1000:])
        return events

    def _history_event_id(self, row: dict[str, Any]) -> str:
        return str(
            row.get("id_str")
            or row.get("rnd")
            or f"{row.get('timeline')}|{row.get('uid')}|{row.get('text')}"
        )

    def _history_row_to_event(self, row: dict[str, Any]) -> Optional[LiveDanmakuEvent]:
        content = str(row.get("text") or "").strip()
        if not content:
            return None
        username = str(row.get("nickname") or row.get("uname") or "观众")
        ts = self._parse_history_timeline(row.get("timeline"))
        raw = {"cmd": "DANMU_MSG_HISTORY", "data": row}
        return LiveDanmakuEvent(
            "danmaku",
            username,
            content,
            ts=ts,
            raw=raw,
            dm_type=_safe_int(row.get("dm_type"), 0),
            emoticon=_normalize_emoticon_options(row.get("emoticon") or {}),
        )

    async def fetch_recent_history_events(
        self, limit: int = 10
    ) -> list[LiveDanmakuEvent]:
        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        events: list[LiveDanmakuEvent] = []
        data = await self._get_json(
            "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory",
            params={
                "roomid": self.real_room_id or self.room_id,
                "room_type": 0,
                "_": int(time.time() * 1000),
            },
        )
        rows = (data.get("data") or {}).get("room") or []
        for row in rows:
            if isinstance(row, dict):
                event = self._history_row_to_event(row)
                if event:
                    events.append(event)
        return events[-max(1, int(limit or 10)) :]

    def _parse_history_timeline(self, value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            return time.time()
        try:
            dt = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            return dt.timestamp()
        except Exception:
            return time.time()

    async def _run_once(self) -> None:
        danmu_info = await self._get_danmu_info()
        token = danmu_info.get("token") or ""
        host_list = danmu_info.get("host_list") or []
        if not host_list:
            raise BilibiliLiveError("弹幕服务器列表为空")

        host = host_list[0]
        ws_host = host.get("host")
        ws_port = host.get("wss_port") or 443
        url = f"wss://{ws_host}:{ws_port}/sub"

        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        logger.info(f"[B站直播] 正在连接房间 {self.real_room_id}: {url}")
        self._ws = await self._session.ws_connect(url, heartbeat=None, timeout=10)
        await self._send_auth(token)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        async for msg in self._ws:
            if self._stop_event.is_set():
                break
            if msg.type == aiohttp.WSMsgType.BINARY:
                payloads = self._unpack_packets(msg.data)
                if self._should_log_binary_packet(msg.data, payloads):
                    self._log_debug(f"收到 WebSocket 二进制包: {len(msg.data)} bytes")
                for payload in payloads:
                    await self._handle_payload(payload)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise BilibiliLiveError(f"WebSocket 错误: {self._ws.exception()}")

    async def _send_auth(self, token: str) -> None:
        payload = {
            "uid": 0,
            "roomid": self.real_room_id or self.room_id,
            "protover": 2,
            "platform": "web",
            "type": 2,
            "key": token,
        }
        await self._send_packet(OP_AUTH, json.dumps(payload).encode("utf-8"), protover=1)

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._send_packet(OP_HEARTBEAT, b"[object Object]", protover=1)
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[B站直播] 心跳停止: {e}")

    async def _send_packet(self, operation: int, body: bytes, protover: int = 1) -> None:
        if not self._ws or self._ws.closed:
            raise BilibiliLiveError("WebSocket 未连接")
        packet_len = HEADER_LEN + len(body)
        header = struct.pack(">IHHII", packet_len, HEADER_LEN, protover, operation, 1)
        await self._ws.send_bytes(header + body)

    def _unpack_packets(self, data: bytes) -> list[dict[str, Any]]:
        packets: list[dict[str, Any]] = []
        offset = 0
        while offset + HEADER_LEN <= len(data):
            packet_len, header_len, protover, operation, _seq = struct.unpack(
                ">IHHII", data[offset : offset + HEADER_LEN]
            )
            if packet_len <= 0:
                break
            body = data[offset + header_len : offset + packet_len]
            offset += packet_len

            if operation == OP_MESSAGE:
                if protover in (0, 1):
                    self._append_json_packet(body, packets)
                elif protover == 2:
                    try:
                        packets.extend(self._unpack_packets(zlib.decompress(body)))
                    except Exception as e:
                        logger.debug(f"[B站直播] zlib 弹幕包解压失败: {e}")
                elif protover == 3 and brotli is not None:
                    try:
                        packets.extend(self._unpack_packets(brotli.decompress(body)))
                    except Exception as e:
                        logger.debug(f"[B站直播] brotli 弹幕包解压失败: {e}")
                elif protover == 3 and brotli is None and not self._warned_missing_brotli:
                    self._warned_missing_brotli = True
                    logger.warning("[B站直播] 收到 brotli 压缩弹幕包，但未安装 brotli，无法解析。请安装 brotli 依赖后重启。")
            elif operation == OP_AUTH_REPLY:
                logger.info(f"[B站直播] 房间 {self.real_room_id} 鉴权成功")
            elif operation == OP_HEARTBEAT_REPLY and len(body) >= 4:
                popularity = struct.unpack(">I", body[:4])[0]
                logger.debug(f"[B站直播] 人气值: {popularity}")

        return packets

    def _is_heartbeat_only_packet(self, data: bytes) -> bool:
        has_packet = False
        offset = 0
        while offset + HEADER_LEN <= len(data):
            try:
                packet_len, header_len, _protover, operation, _seq = struct.unpack(
                    ">IHHII", data[offset : offset + HEADER_LEN]
                )
            except Exception:
                return False
            if packet_len <= 0 or header_len < HEADER_LEN or offset + packet_len > len(data):
                return False
            has_packet = True
            if operation != OP_HEARTBEAT_REPLY:
                return False
            offset += packet_len
        return has_packet and offset == len(data)

    def _should_log_binary_packet(self, data: bytes, payloads: list[dict[str, Any]]) -> bool:
        if self._is_heartbeat_only_packet(data):
            return False
        if not payloads:
            return True
        return any(
            not self._is_ignored_debug_cmd(str(payload.get("cmd", "")).split(":")[0])
            for payload in payloads
        )

    def _log_debug(self, message: str) -> None:
        if self.debug_log:
            logger.info(f"[B站直播调试] {message}")
        else:
            logger.debug(f"[B站直播] {message}")

    def _append_json_packet(self, body: bytes, packets: list[dict[str, Any]]) -> None:
        try:
            text = body.decode("utf-8", errors="ignore").strip("\x00")
            if text:
                packets.append(json.loads(text))
        except Exception as e:
            logger.debug(f"[B站直播] JSON 弹幕包解析失败: {e}")

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        cmd = str(payload.get("cmd", "")).split(":")[0]
        should_log_cmd = not self._is_ignored_debug_cmd(cmd)
        if should_log_cmd:
            self._log_debug(f"收到事件 cmd={cmd}")
        event = self._payload_to_event(payload)
        if event:
            await self.on_event(event)
        elif cmd and should_log_cmd:
            self._log_debug(f"未映射事件 cmd={cmd}")

    def _is_ignored_debug_cmd(self, cmd: str) -> bool:
        return cmd in {
            "NOTICE_MSG",
            "ONLINE_RANK_COUNT",
            "ONLINE_RANK_V3",
            "STOP_LIVE_ROOM_LIST",
        }

    def _payload_to_event(self, payload: dict[str, Any]) -> Optional[LiveDanmakuEvent]:
        cmd = str(payload.get("cmd", "")).split(":")[0]

        if cmd == "DANMU_MSG":
            info = payload.get("info") or []
            try:
                content = str(info[1])
                username = str(info[2][1])
            except Exception:
                return None
            info0 = (
                info[0]
                if isinstance(info, list) and info and isinstance(info[0], list)
                else []
            )
            return LiveDanmakuEvent(
                "danmaku",
                username,
                content,
                raw=payload,
                dm_type=_safe_int(info0[12]) if len(info0) > 12 else 0,
                emoticon=(
                    _normalize_emoticon_options(info0[13])
                    if len(info0) > 13
                    else {}
                ),
            )

        if cmd == "SEND_GIFT":
            data = payload.get("data") or {}
            username = str(data.get("uname") or "观众")
            gift_name = str(data.get("giftName") or "礼物")
            num = data.get("num") or 1
            return LiveDanmakuEvent("gift", username, f"赠送 {gift_name} x{num}", raw=payload)

        if cmd == "SUPER_CHAT_MESSAGE":
            data = payload.get("data") or {}
            user_info = data.get("user_info") or {}
            username = str(user_info.get("uname") or data.get("uname") or "观众")
            message = str(data.get("message") or "")
            price = data.get("price")
            prefix = f"发送醒目留言 {price}元" if price else "发送醒目留言"
            return LiveDanmakuEvent("super_chat", username, f"{prefix}: {message}", raw=payload)

        if cmd == "INTERACT_WORD":
            data = payload.get("data") or {}
            username = str(data.get("uname") or "观众")
            msg_type = data.get("msg_type")
            if msg_type == 1:
                return LiveDanmakuEvent("enter_room", username, "进入直播间", raw=payload)
            if msg_type == 2:
                return LiveDanmakuEvent("follow", username, "关注了直播间", raw=payload)

        if cmd == "LIKE_INFO_V3_CLICK":
            data = payload.get("data") or {}
            username = str(data.get("uname") or "观众")
            return LiveDanmakuEvent("like", username, "点赞了直播间", raw=payload)

        return None


class BilibiliLaplaceClient:
    """Client for LAPLACE Event Bridge (default ws://localhost:9696)."""

    def __init__(
        self,
        bridge_url: str,
        on_event: Callable[[LiveDanmakuEvent], Awaitable[None]],
        room_id: int = 0,
        token: str = "",
        reconnect_interval: float = 3.0,
        debug_log: bool = False,
    ):
        self.bridge_url = (bridge_url or "ws://localhost:9696").strip()
        self.room_id = int(room_id or 0)
        self.token = (token or "").strip()
        self.on_event = on_event
        self.reconnect_interval = max(1.0, float(reconnect_interval or 3.0))
        self.debug_log = debug_log
        self.real_room_id: Optional[int] = None
        self.last_error: str = ""

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    async def run_forever(self) -> None:
        self._stop_event.clear()
        self.last_error = ""
        self._session = aiohttp.ClientSession(headers=HTTP_HEADERS)
        try:
            while not self._stop_event.is_set():
                try:
                    await self._run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.last_error = str(e)
                    if self._stop_event.is_set():
                        break
                    logger.warning(f"[Laplace直播] Event Bridge 连接异常，准备重连: {e}")
                    await asyncio.sleep(self.reconnect_interval)
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stop_event.set()
        try:
            ws = self._ws
            self._ws = None
            if ws and not ws.closed:
                try:
                    await ws.close()
                except Exception as exc:
                    logger.debug(f"[Laplace直播] 关闭 WebSocket 异常: {exc}")
        finally:
            session = self._session
            self._session = None
            if session and not session.closed:
                await session.close()

    def _build_url(self) -> str:
        url = self.bridge_url
        if "://" not in url:
            url = f"ws://{url}"
        if self.token:
            parsed = urllib.parse.urlsplit(url)
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            query = [(key, value) for key, value in query if key != "token"]
            query.append(("token", self.token))
            url = urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urllib.parse.urlencode(query),
                    parsed.fragment,
                )
            )
        if self.room_id:
            parsed = urllib.parse.urlsplit(url)
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not any(key == "rooms" for key, _value in query):
                query.append(("rooms", str(self.room_id)))
            url = urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    urllib.parse.urlencode(query),
                    parsed.fragment,
                )
            )
        return url

    async def _run_once(self) -> None:
        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")

        url = self._build_url()
        protocols = ["laplace-event-bridge-role-client", self.token] if self.token else ()
        safe_url = self._redact_url(url)
        logger.info(f"[Laplace直播] 正在连接 Event Bridge: {safe_url}")
        self._ws = await self._session.ws_connect(
            url,
            protocols=protocols,
            heartbeat=None,
            timeout=10,
        )
        logger.info("[Laplace直播] Event Bridge 已连接")

        async for msg in self._ws:
            if self._stop_event.is_set():
                break
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_text(msg.data)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                await self._handle_text(msg.data.decode("utf-8", errors="ignore"))
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise BilibiliLiveError(f"Event Bridge 错误: {self._ws.exception()}")

    def _redact_url(self, url: str) -> str:
        if "token=" not in url:
            return url
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        safe_query = [
            (key, "***" if key == "token" else value)
            for key, value in query
        ]
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urllib.parse.urlencode(safe_query),
                parsed.fragment,
            )
        )

    async def _handle_text(self, text: str) -> None:
        try:
            payload = json.loads(text)
        except Exception as e:
            logger.debug(f"[Laplace直播] JSON 解析失败: {e}")
            return

        event_type = str(payload.get("type") or "")
        if self.debug_log:
            logger.info(f"[Laplace直播调试] 收到事件 type={event_type}")

        if event_type == "ping":
            await self._send_pong(payload)
            return
        if event_type == "established":
            logger.info(
                f"[Laplace直播] Event Bridge 握手成功 client={payload.get('clientId') or 'unknown'}"
            )
            return

        event = self._payload_to_event(payload)
        if event:
            await self.on_event(event)

    async def _send_pong(self, payload: dict[str, Any]) -> None:
        if not self._ws or self._ws.closed:
            return
        await self._ws.send_str(
            json.dumps(
                {
                    "type": "pong",
                    "timestamp": int(time.time() * 1000),
                    "respondingTo": payload.get("timestamp"),
                },
                ensure_ascii=False,
            )
        )

    def _payload_to_event(self, payload: dict[str, Any]) -> Optional[LiveDanmakuEvent]:
        event_type = str(payload.get("type") or "")
        username = str(payload.get("username") or payload.get("userName") or "观众")
        message = str(payload.get("message") or payload.get("text") or "")

        if event_type == "message":
            return LiveDanmakuEvent(
                "danmaku",
                username,
                message,
                raw=payload,
                dm_type=_safe_int(payload.get("dm_type"), 0),
                emoticon=_normalize_emoticon_options(payload.get("emoticon") or {}),
            )

        if event_type == "superchat":
            price = payload.get("priceNormalized") or payload.get("price")
            prefix = f"发送醒目留言 {price}元" if price else "发送醒目留言"
            return LiveDanmakuEvent("super_chat", username, f"{prefix}: {message}", raw=payload)

        if event_type == "gift":
            return LiveDanmakuEvent("gift", username, message or "赠送礼物", raw=payload)

        if event_type == "toast":
            return LiveDanmakuEvent("gift", username, message or "触发高亮事件", raw=payload)

        if event_type == "like-click":
            return LiveDanmakuEvent("like", username, message or "点赞了直播间", raw=payload)

        if event_type == "interaction":
            action_map = {
                1: ("enter_room", "进入直播间"),
                2: ("follow", "关注了直播间"),
                3: ("share", "分享了直播间"),
                4: ("follow", "特别关注了直播间"),
                5: ("follow", "互相关注了直播间"),
            }
            try:
                action = int(payload.get("action") or 0)
            except (TypeError, ValueError):
                action = 0
            mapped_type, mapped_text = action_map.get(action, ("interaction", message or "互动"))
            return LiveDanmakuEvent(mapped_type, username, mapped_text, raw=payload)

        if event_type == "entry-effect":
            cleaned = re.sub(r"<%([^%>]+)%>", r"\1", message).strip()
            return LiveDanmakuEvent("enter_room", username, cleaned or "进入直播间", raw=payload)

        if event_type == "system" and self.debug_log:
            return LiveDanmakuEvent("system", "系统", message, raw=payload)

        return None


def parse_cookie_string(cookie_text: str = "") -> dict[str, str] | None:
    cookie_text = (cookie_text or "").strip()
    if not cookie_text:
        return None

    cookies: dict[str, str] = {}
    for item in cookie_text.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value

    if not cookies and cookie_text:
        cookies["SESSDATA"] = cookie_text
    if "SESSDATA" not in cookies and cookie_text and ";" not in cookie_text and "=" not in cookie_text:
        cookies["SESSDATA"] = cookie_text
    return cookies or None


def normalize_cookie_string(cookie_text: str = "") -> str:
    """Return a cookie string accepted by both the builtin and blivedm clients."""
    cookie_text = (cookie_text or "").strip()
    if not cookie_text:
        return ""
    if ";" not in cookie_text and "=" not in cookie_text:
        return f"SESSDATA={cookie_text}"
    return cookie_text


async def probe_bilibili_live_room(room_id: int, sessdata: str = "") -> dict[str, Any]:
    headers = HTTP_HEADERS
    async with aiohttp.ClientSession(
        headers=headers,
        cookies=parse_cookie_string(sessdata),
    ) as session:
        async with session.get(
            "https://api.live.bilibili.com/room/v1/Room/room_init",
            params={"id": int(room_id)},
            timeout=10,
        ) as resp:
            resp.raise_for_status()
            room_init = await resp.json(content_type=None)

        real_room_id = int((room_init.get("data") or {}).get("room_id") or room_id)
        mixin_key = await fetch_wbi_mixin_key(session)
        signed_params = encode_wbi_params({"id": real_room_id, "type": 0}, mixin_key)

        async with session.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
            params=signed_params,
            timeout=10,
        ) as resp:
            resp.raise_for_status()
            danmu_info = await resp.json(content_type=None)

    host_list = (danmu_info.get("data") or {}).get("host_list") or []
    return {
        "room_init_code": room_init.get("code"),
        "room_init_message": room_init.get("message"),
        "real_room_id": real_room_id,
        "live_status": (room_init.get("data") or {}).get("live_status"),
        "danmu_info_code": danmu_info.get("code"),
        "danmu_info_message": danmu_info.get("message"),
        "danmu_risk_control": int(danmu_info.get("code") or 0) == -352,
        "danmu_token_present": bool((danmu_info.get("data") or {}).get("token")),
        "danmu_host_count": len(host_list),
        "danmu_hosts": [
            f"{host.get('host')}:{host.get('wss_port') or host.get('ws_port') or ''}"
            for host in host_list[:3]
        ],
    }


class BilibiliOpenLiveClient:
    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        app_id: int,
        room_owner_auth_code: str,
        on_event: Callable[[LiveDanmakuEvent], Awaitable[None]],
        reconnect_interval: float = 5.0,
        websocket_heartbeat_interval: float = 30.0,
        game_heartbeat_interval: float = 20.0,
    ):
        self.access_key_id = access_key_id.strip()
        self.access_key_secret = access_key_secret.strip()
        self.app_id = int(app_id)
        self.room_owner_auth_code = room_owner_auth_code.strip()
        self.on_event = on_event
        self.reconnect_interval = reconnect_interval
        self.websocket_heartbeat_interval = websocket_heartbeat_interval
        self.game_heartbeat_interval = game_heartbeat_interval

        self.real_room_id: Optional[int] = None
        self.game_id: Optional[str] = None
        self.anchor_open_id: Optional[str] = None
        self._auth_body = ""
        self._wss_links: list[str] = []

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._stop_event = asyncio.Event()
        self._ws_heartbeat_task: Optional[asyncio.Task] = None
        self._game_heartbeat_task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    async def run_forever(self) -> None:
        self._stop_event.clear()
        self._session = aiohttp.ClientSession(headers=HTTP_HEADERS)
        try:
            while not self._stop_event.is_set():
                try:
                    await self._start_game()
                    self._game_heartbeat_task = asyncio.create_task(
                        self._game_heartbeat_loop()
                    )
                    await self._run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if self._stop_event.is_set():
                        break
                    logger.warning(f"[B站开放平台] 监听连接异常，准备重连: {e}")
                    await asyncio.sleep(self.reconnect_interval)
                finally:
                    await self._cancel_runtime_tasks()
                    await self._close_ws()
                    await self._end_game()
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._stop_event.set()
        try:
            try:
                await self._cancel_runtime_tasks()
            except Exception as exc:
                logger.debug(f"[B站开放平台] 取消运行任务异常: {exc}")
            try:
                await self._close_ws()
            except Exception as exc:
                logger.debug(f"[B站开放平台] 关闭 WebSocket 异常: {exc}")
            try:
                await self._end_game()
            except Exception as exc:
                logger.debug(f"[B站开放平台] 结束场次异常: {exc}")
        finally:
            session = self._session
            self._session = None
            if session and not session.closed:
                await session.close()

    async def _cancel_runtime_tasks(self) -> None:
        for task in (self._ws_heartbeat_task, self._game_heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._ws_heartbeat_task = None
        self._game_heartbeat_task = None

    async def _close_ws(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    def _build_open_live_headers(self, body_bytes: bytes) -> dict[str, str]:
        headers = {
            "x-bili-accesskeyid": self.access_key_id,
            "x-bili-content-md5": hashlib.md5(body_bytes).hexdigest(),
            "x-bili-signature-method": "HMAC-SHA256",
            "x-bili-signature-nonce": uuid.uuid4().hex,
            "x-bili-signature-version": "1.0",
            "x-bili-timestamp": str(int(datetime.datetime.now().timestamp())),
        }
        str_to_sign = "\n".join(f"{key}:{value}" for key, value in headers.items())
        signature = hmac.new(
            self.access_key_secret.encode("utf-8"),
            str_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers["Authorization"] = signature
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        return headers

    async def _request_open_live(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        body_bytes = json.dumps(body).encode("utf-8")
        headers = self._build_open_live_headers(body_bytes)
        async with self._session.post(
            url, headers=headers, data=body_bytes, timeout=10
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if data.get("code") != 0:
            raise BilibiliLiveError(
                f"开放平台请求失败: code={data.get('code')}, message={data.get('message')}, request_id={data.get('request_id')}"
            )
        return data.get("data") or {}

    async def _start_game(self) -> None:
        data = await self._request_open_live(
            OPEN_LIVE_START_URL,
            {"code": self.room_owner_auth_code, "app_id": self.app_id},
        )
        game_info = data.get("game_info") or {}
        websocket_info = data.get("websocket_info") or {}
        anchor_info = data.get("anchor_info") or {}

        self.game_id = str(game_info.get("game_id") or "")
        self._auth_body = str(websocket_info.get("auth_body") or "")
        self._wss_links = list(websocket_info.get("wss_link") or [])
        self.real_room_id = int(anchor_info.get("room_id") or 0) or None
        self.anchor_open_id = anchor_info.get("open_id")

        if not self._auth_body or not self._wss_links:
            raise BilibiliLiveError("开放平台启动成功但未返回弹幕服务器认证信息")
        logger.info(
            f"[B站开放平台] 场次已启动，房间={self.real_room_id or '未知'}，game_id={self.game_id or '空'}"
        )

    async def _end_game(self) -> None:
        if not self.game_id:
            return
        game_id = self.game_id
        self.game_id = None
        try:
            await self._request_open_live(
                OPEN_LIVE_END_URL,
                {"app_id": self.app_id, "game_id": game_id},
            )
            logger.info(f"[B站开放平台] 场次已关闭: {game_id}")
        except BilibiliLiveError as e:
            message = str(e)
            if "code=7000" in message or "code=7003" in message:
                logger.info(f"[B站开放平台] 场次已处于关闭状态: {game_id}")
                return
            logger.warning(f"[B站开放平台] 关闭场次失败: {e}")
        except Exception as e:
            logger.warning(f"[B站开放平台] 关闭场次异常: {e}")

    async def _game_heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(self.game_heartbeat_interval)
                await self._send_game_heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[B站开放平台] 项目心跳停止: {e}")
            await self._close_ws()

    async def _send_game_heartbeat(self) -> None:
        if not self.game_id:
            return
        try:
            await self._request_open_live(
                OPEN_LIVE_HEARTBEAT_URL, {"game_id": self.game_id}
            )
            logger.debug(f"[B站开放平台] 项目心跳成功: {self.game_id}")
        except Exception as e:
            logger.warning(f"[B站开放平台] 项目心跳失败: {e}")
            await self._close_ws()

    async def _run_once(self) -> None:
        if not self._wss_links:
            raise BilibiliLiveError("开放平台弹幕服务器列表为空")

        if not self._session:
            raise BilibiliLiveError("HTTP session 未初始化")
        url = self._wss_links[0]
        logger.info(f"[B站开放平台] 正在连接弹幕服务器: {url}")
        self._ws = await self._session.ws_connect(url, heartbeat=None, timeout=10)
        await self._send_auth()
        self._ws_heartbeat_task = asyncio.create_task(self._ws_heartbeat_loop())

        async for msg in self._ws:
            if self._stop_event.is_set():
                break
            if msg.type == aiohttp.WSMsgType.BINARY:
                for payload in self._unpack_packets(msg.data):
                    await self._handle_payload(payload)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise BilibiliLiveError(f"WebSocket 错误: {self._ws.exception()}")

    async def _send_auth(self) -> None:
        await self._send_packet(OP_AUTH, self._auth_body.encode("utf-8"), protover=0)

    async def _ws_heartbeat_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._send_packet(OP_HEARTBEAT, b"{}", protover=0)
                await asyncio.sleep(self.websocket_heartbeat_interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[B站开放平台] WebSocket 心跳停止: {e}")

    async def _send_packet(self, operation: int, body: bytes, protover: int = 0) -> None:
        if not self._ws or self._ws.closed:
            raise BilibiliLiveError("WebSocket 未连接")
        packet_len = HEADER_LEN + len(body)
        header = struct.pack(">IHHII", packet_len, HEADER_LEN, protover, operation, 1)
        await self._ws.send_bytes(header + body)

    def _unpack_packets(self, data: bytes) -> list[dict[str, Any]]:
        packets: list[dict[str, Any]] = []
        offset = 0
        while offset + HEADER_LEN <= len(data):
            packet_len, header_len, protover, operation, _seq = struct.unpack(
                ">IHHII", data[offset : offset + HEADER_LEN]
            )
            if packet_len <= 0:
                break
            body = data[offset + header_len : offset + packet_len]
            offset += packet_len

            if operation == OP_MESSAGE:
                if protover in (0, 1):
                    self._append_json_packet(body, packets)
                elif protover == 2:
                    try:
                        packets.extend(self._unpack_packets(zlib.decompress(body)))
                    except Exception as e:
                        logger.debug(f"[B站开放平台] zlib 弹幕包解压失败: {e}")
                elif protover == 3 and brotli is not None:
                    try:
                        packets.extend(self._unpack_packets(brotli.decompress(body)))
                    except Exception as e:
                        logger.debug(f"[B站开放平台] brotli 弹幕包解压失败: {e}")
            elif operation == OP_AUTH_REPLY:
                self._handle_auth_reply(body)
            elif operation == OP_HEARTBEAT_REPLY and len(body) >= 4:
                popularity = struct.unpack(">I", body[:4])[0]
                logger.debug(f"[B站开放平台] 人气值: {popularity}")

        return packets

    def _handle_auth_reply(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            logger.debug(f"[B站开放平台] 鉴权响应解析失败: {body!r}")
            return
        if data.get("code") != 0:
            raise BilibiliLiveError(f"开放平台 WebSocket 鉴权失败: {data}")
        logger.info("[B站开放平台] WebSocket 鉴权成功")

    def _append_json_packet(self, body: bytes, packets: list[dict[str, Any]]) -> None:
        try:
            text = body.decode("utf-8", errors="ignore").strip("\x00")
            if text:
                packets.append(json.loads(text))
        except Exception as e:
            logger.debug(f"[B站开放平台] JSON 弹幕包解析失败: {e}")

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        if str(payload.get("cmd", "")).split(":")[0] == "LIVE_OPEN_PLATFORM_INTERACTION_END":
            data = payload.get("data") or {}
            if data.get("game_id") == self.game_id:
                logger.warning("[B站开放平台] 服务端结束当前场次，准备重连")
                await self._close_ws()
            return

        event = self._payload_to_event(payload)
        if event:
            await self.on_event(event)

    def _payload_to_event(self, payload: dict[str, Any]) -> Optional[LiveDanmakuEvent]:
        cmd = str(payload.get("cmd", "")).split(":")[0]
        data = payload.get("data") or {}

        if cmd in {"LIVE_OPEN_PLATFORM_DM", "LIVE_OPEN_PLATFORM_DM_MIRROR"}:
            username = str(data.get("uname") or "观众")
            content = str(data.get("msg") or "")
            dm_type = _safe_int(data.get("dm_type"), 0)
            emoticon: dict[str, Any] = {}
            if dm_type == 1:
                emoticon = {
                    "url": str(data.get("emoji_img_url") or ""),
                    "emoticon_unique": str(data.get("emoticon_unique") or ""),
                }
            return LiveDanmakuEvent(
                "danmaku",
                username,
                content,
                raw=payload,
                dm_type=dm_type,
                emoticon=emoticon,
            )

        if cmd == "LIVE_OPEN_PLATFORM_SEND_GIFT":
            username = str(data.get("uname") or "观众")
            gift_name = str(data.get("gift_name") or "礼物")
            gift_num = data.get("gift_num") or 1
            return LiveDanmakuEvent(
                "gift", username, f"赠送 {gift_name} x{gift_num}", raw=payload
            )

        if cmd == "LIVE_OPEN_PLATFORM_SUPER_CHAT":
            username = str(data.get("uname") or "观众")
            message = str(data.get("message") or "")
            rmb = data.get("rmb")
            prefix = f"发送醒目留言 {rmb}元" if rmb else "发送醒目留言"
            return LiveDanmakuEvent(
                "super_chat", username, f"{prefix}: {message}", raw=payload
            )

        if cmd == "LIVE_OPEN_PLATFORM_GUARD":
            user_info = data.get("user_info") or {}
            username = str(user_info.get("uname") or "观众")
            guard_level = data.get("guard_level") or ""
            guard_num = data.get("guard_num") or 1
            guard_unit = data.get("guard_unit") or "月"
            return LiveDanmakuEvent(
                "buy_guard",
                username,
                f"购买大航海 等级={guard_level} x{guard_num}{guard_unit}",
                raw=payload,
            )

        if cmd == "LIVE_OPEN_PLATFORM_LIKE":
            username = str(data.get("uname") or "观众")
            like_text = str(data.get("like_text") or "点赞了直播间")
            like_count = data.get("like_count")
            suffix = f" x{like_count}" if like_count else ""
            return LiveDanmakuEvent("like", username, f"{like_text}{suffix}", raw=payload)

        if cmd == "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER":
            username = str(data.get("uname") or "观众")
            return LiveDanmakuEvent("enter_room", username, "进入直播间", raw=payload)

        if cmd == "LIVE_OPEN_PLATFORM_LIVE_START":
            title = str(data.get("title") or "")
            return LiveDanmakuEvent("live_start", "系统", f"直播开始 {title}".strip(), raw=payload)

        if cmd == "LIVE_OPEN_PLATFORM_LIVE_END":
            return LiveDanmakuEvent("live_end", "系统", "直播结束", raw=payload)

        return None
