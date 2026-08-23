# -*- coding: utf-8 -*-
"""QQ Zone feed parsing, queries, and direct post actions."""
from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .helpers import _safe_float, _safe_int, _single_line
from .persona_config import runtime_persona_setting
from .qzone_recent_parser import is_official_qzone_promotion

__all__ = ("QzoneFeedMixin",)

class QzoneFeedMixin:
    """Feed parsing, retrieval, and direct post mutation helpers."""
    @staticmethod
    def _qzone_norm_key(key: Any) -> str:
        return str(key or "").strip().lower().replace("-", "_")

    @classmethod
    def _qzone_comment_content(cls, item: dict[str, Any]) -> str:
        normalized = {cls._qzone_norm_key(key): value for key, value in (item or {}).items()}
        raw = ""
        for key in ("content", "comment", "text", "msg", "con", "html"):
            value = normalized.get(key)
            if value not in (None, ""):
                raw = str(value)
                break
        if not raw:
            return ""
        cleaned = html.unescape(re.sub(r"<[^>]+>", "", raw))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return _single_line(cleaned, 180)

    @classmethod
    def _qzone_comment_identity(cls, item: dict[str, Any]) -> tuple[int, str]:
        normalized = {cls._qzone_norm_key(key): value for key, value in (item or {}).items()}
        raw_uin = str(normalized.get("uin") or normalized.get("user_uin") or normalized.get("qq") or normalized.get("uin_str") or "").strip().lstrip("oO")
        uin = _safe_int(raw_uin, 0, 0)
        name = ""
        for key in ("name", "nickname", "nick", "user_name", "username"):
            value = normalized.get(key)
            if value not in (None, ""):
                name = _single_line(value, 40)
                break
        return uin, name

    @classmethod
    def _qzone_comment_time(cls, item: dict[str, Any]) -> float:
        normalized = {cls._qzone_norm_key(key): value for key, value in (item or {}).items()}
        for key in ("create_time", "created_time", "time", "timestamp", "abstime", "pubtime"):
            value = normalized.get(key)
            if value not in (None, ""):
                return _safe_float(value, 0)
        return 0.0

    @classmethod
    def _qzone_comment_id(cls, post_tid: str, item: dict[str, Any]) -> str:
        normalized = {cls._qzone_norm_key(key): value for key, value in (item or {}).items()}
        for key in ("commentid", "comment_id", "cid", "id", "tid", "replyid", "reply_id", "cellid", "rootid"):
            value = normalized.get(key)
            if value not in (None, ""):
                return f"{post_tid or 'post'}:{_single_line(value, 80)}"
        return cls._qzone_comment_fingerprint(post_tid, item)

    @classmethod
    def _qzone_comment_legacy_fallback_id(cls, post_tid: str, item: dict[str, Any]) -> str:
        uin, name = cls._qzone_comment_identity(item)
        content = cls._qzone_comment_content(item)
        created = cls._qzone_comment_time(item)
        digest = hashlib.sha1(f"{post_tid}|{uin}|{name}|{content}|{created}".encode("utf-8", "ignore")).hexdigest()[:20]
        return f"{post_tid or 'post'}:sha1:{digest}"

    @classmethod
    def _qzone_comment_fingerprint(cls, post_tid: str, item: dict[str, Any]) -> str:
        uin, name = cls._qzone_comment_identity(item)
        content = cls._qzone_comment_content(item)
        author = str(uin or "").strip()
        if not author:
            author = re.sub(r"\s+", "", _single_line(name, 40).lower()) or "unknown"
        normalized_content = re.sub(r"\s+", "", _single_line(content, 180)).lower()
        digest = hashlib.sha1(f"{post_tid or 'post'}|{author}|{normalized_content}".encode("utf-8", "ignore")).hexdigest()[:20]
        return f"{post_tid or 'post'}:fp:{digest}"

    @classmethod
    def _qzone_looks_like_comment(cls, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        if not cls._qzone_comment_content(item):
            return False
        normalized = {cls._qzone_norm_key(key) for key in item.keys()}
        identity_keys = {
            "uin",
            "user_uin",
            "qq",
            "name",
            "nickname",
            "nick",
            "commentid",
            "comment_id",
            "cid",
            "replyid",
            "reply_id",
            "create_time",
            "created_time",
            "abstime",
        }
        return bool(normalized & identity_keys)

    @classmethod
    def _qzone_collect_comment_items(
        cls,
        payload: Any,
        *,
        _depth: int = 0,
        _inside_comment_branch: bool = False,
    ) -> list[dict[str, Any]]:
        if payload is None or _depth > 5:
            return []
        if isinstance(payload, list):
            items: list[dict[str, Any]] = []
            for entry in payload:
                if cls._qzone_looks_like_comment(entry):
                    items.append(entry)
                elif isinstance(entry, (dict, list)):
                    items.extend(
                        cls._qzone_collect_comment_items(
                            entry,
                            _depth=_depth + 1,
                            _inside_comment_branch=_inside_comment_branch,
                        )
                    )
            return items
        if not isinstance(payload, dict):
            return []
        items: list[dict[str, Any]] = []
        if _inside_comment_branch and cls._qzone_looks_like_comment(payload):
            return [payload]
        for key, value in payload.items():
            norm = cls._qzone_norm_key(key)
            is_comment_branch = _inside_comment_branch or any(token in norm for token in ("comment", "reply"))
            if not is_comment_branch:
                continue
            if cls._qzone_looks_like_comment(value):
                items.append(value)
            elif isinstance(value, (dict, list)):
                items.extend(
                    cls._qzone_collect_comment_items(
                        value,
                        _depth=_depth + 1,
                        _inside_comment_branch=True,
                    )
                )
        return items

    @classmethod
    def _qzone_parse_comments_from_msg(cls, msg: dict[str, Any]) -> list[Any]:
        post_tid = str(msg.get("tid") or "")
        seen: set[str] = set()
        comments: list[Any] = []
        for item in cls._qzone_collect_comment_items(msg):
            if not isinstance(item, dict):
                continue
            content = cls._qzone_comment_content(item)
            if not content:
                continue
            comment_id = cls._qzone_comment_id(post_tid, item)
            if comment_id in seen:
                continue
            seen.add(comment_id)
            uin, name = cls._qzone_comment_identity(item)
            comment_key = cls._qzone_comment_fingerprint(post_tid, item)
            comments.append(
                SimpleNamespace(
                    comment_id=comment_id,
                    comment_key=comment_key,
                    comment_legacy_id=cls._qzone_comment_legacy_fallback_id(post_tid, item),
                    uin=uin,
                    name=name,
                    content=content,
                    create_time=cls._qzone_comment_time(item),
                    raw=item,
                )
            )
        comments.sort(key=lambda item: _safe_float(getattr(item, "create_time", 0), 0))
        return comments

    def _qzone_parse_feeds(self, msglist: list[Any]) -> list[Any]:
        posts: list[Any] = []
        for msg in msglist:
            if not isinstance(msg, dict):
                continue
            if is_official_qzone_promotion(msg.get("name") or msg.get("nickname") or msg.get("nick")):
                continue
            images: list[str] = []
            for image in msg.get("pic", []) if isinstance(msg.get("pic"), list) else []:
                if not isinstance(image, dict):
                    continue
                for key in ("url2", "url3", "url1", "smallurl"):
                    raw = image.get(key)
                    if raw:
                        images.append(str(raw))
                        break
            for video in msg.get("video", []) if isinstance(msg.get("video"), list) else []:
                if isinstance(video, dict) and (video.get("url1") or video.get("pic_url")):
                    images.append(str(video.get("url1") or video.get("pic_url")))
            posts.append(
                SimpleNamespace(
                    tid=str(msg.get("tid") or ""),
                    uin=int(msg.get("uin") or 0),
                    name=str(msg.get("name") or ""),
                    text=str(msg.get("content") or "").strip(),
                    rt_con=str((msg.get("rt_con") or {}).get("content") or "") if isinstance(msg.get("rt_con"), dict) else "",
                    images=images,
                    comments=self._qzone_parse_comments_from_msg(msg),
                    create_time=msg.get("created_time") or 0,
                    appid=str(msg.get("appid") or "311"),
                    typeid=str(msg.get("typeid") or msg.get("type") or "0"),
                    abstime=_safe_int(msg.get("created_time") or msg.get("abstime"), 0, 0),
                    fid=str(msg.get("tid") or msg.get("fid") or ""),
                    unikey=str(msg.get("unikey") or msg.get("likeKey") or msg.get("like_key") or ""),
                    curkey=str(msg.get("curkey") or msg.get("curlikekey") or msg.get("likeKey") or msg.get("like_key") or ""),
                    raw=msg,
                    status="approved",
                )
            )
        return posts

    @staticmethod
    def _qzone_post_value(post: Any, key: str, default: Any = "") -> Any:
        value = getattr(post, key, None)
        if value not in (None, ""):
            return value
        raw = getattr(post, "raw", None)
        if isinstance(raw, dict):
            value = raw.get(key)
            if value not in (None, ""):
                return value
        return default

    def _qzone_post_like_url(self, post: Any, *, uin: str, tid: str) -> str:
        raw = getattr(post, "raw", None)
        for key in ("unikey", "curkey", "curlikekey", "likeKey", "like_key", "url"):
            value = getattr(post, key, None)
            if value not in (None, ""):
                return str(value)
            if isinstance(raw, dict) and raw.get(key) not in (None, ""):
                return str(raw.get(key))
        html_text = str(raw.get("html") or "") if isinstance(raw, dict) else ""
        if html_text:
            for attr in ("data-unikey", "data-curkey", "unikey", "curkey"):
                match = re.search(rf"""{re.escape(attr)}\s*=\s*["']([^"']+)["']""", html_text, flags=re.IGNORECASE)
                if match:
                    return html.unescape(match.group(1)).strip()
        return f"https://user.qzone.qq.com/{uin}/mood/{tid}"

    async def _qzone_query_feeds(
        self,
        event: AstrMessageEvent | None = None,
        *,
        target_id: str | None = None,
        pos: int = 0,
        num: int = 1,
        with_detail: bool = False,
        cookie_header: str | None = None,
    ) -> list[Any]:
        if cookie_header is None:
            cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        target = _single_line(target_id, 40)
        if not target:
            target = str(ctx["uin"])
        payload = await self._qzone_request(
            event,
            "GET",
            "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
            params={
                "g_tk": ctx["gtk"],
                "uin": target,
                "ftype": 0,
                "sort": 0,
                "pos": max(0, int(pos or 0)),
                "num": max(1, int(num or 1)),
                "replynum": 100,
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "json",
                "need_comment": 1 if with_detail else 0,
                "need_private_comment": 1 if with_detail else 0,
            },
            cookie_header=cookie_header,
        )
        # Nested {"code": -3000, "data": {}} responses lose the outer code during
        # normalization, so fall back to ret/_raw_code or a failure reads as success.
        code = self._qzone_response_code(payload)
        if code not in {0, "0"}:
            raise RuntimeError(
                _single_line(
                    payload.get("message") or payload.get("msg") or payload.get("_raw_message") or f"查询失败 code={code}",
                    160,
                )
            )
        msglist = payload.get("msglist") or []
        if not isinstance(msglist, list):
            msglist = []
        return self._qzone_parse_feeds(msglist)

    async def _qzone_verify_like_post(
        self,
        event: AstrMessageEvent | None,
        post: Any,
        *,
        cookie_header: str,
        target_liked: bool = True,
    ) -> dict[str, Any]:
        tid = str(getattr(post, "tid", "") or "")
        uin = str(getattr(post, "uin", "") or "")
        fid = str(self._qzone_post_value(post, "fid", tid) or tid)
        if not tid or not uin:
            return {"verified": False, "liked": None, "message": "缺少说说 tid 或 uin，无法反查点赞状态"}
        for attempt, delay in enumerate((0.0, 0.45, 1.2), start=1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                feeds = await self._qzone_query_feeds(
                    event,
                    target_id=uin,
                    pos=0,
                    num=10,
                    with_detail=False,
                    cookie_header=cookie_header,
                )
            except Exception as exc:
                if attempt >= 3:
                    return {"verified": False, "liked": None, "message": f"点赞反查失败：{_single_line(exc, 120)}"}
                continue
            for feed in feeds:
                feed_tid = str(getattr(feed, "tid", "") or "")
                feed_fid = str(self._qzone_post_value(feed, "fid", feed_tid) or feed_tid)
                if (tid and feed_tid == tid) or (fid and feed_fid == fid):
                    liked = bool(getattr(feed, "liked", False))
                    try:
                        setattr(post, "liked", liked)
                    except Exception:
                        pass
                    if liked == bool(target_liked):
                        return {"verified": True, "liked": liked, "message": "已反查到点赞状态"}
                    if attempt >= 3:
                        return {
                            "verified": False,
                            "liked": liked,
                            "message": "点赞请求已受理，但最近动态反查到的状态仍未变化",
                        }
            if attempt >= 3:
                return {"verified": False, "liked": None, "message": "点赞请求已受理，但最近动态中暂未反查到这条说说"}
        return {"verified": False, "liked": None, "message": "点赞请求已受理，但暂未完成反查"}

    async def _qzone_like_post(self, event: AstrMessageEvent | None, post: Any) -> dict[str, Any]:
        cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        tid = str(getattr(post, "tid", "") or "")
        uin = str(getattr(post, "uin", "") or "")
        if not tid or not uin:
            raise RuntimeError("说说 tid 或 uin 为空，无法点赞")
        like_url = self._qzone_post_like_url(post, uin=uin, tid=tid)
        curkey = str(self._qzone_post_value(post, "curkey", "") or "") or like_url
        unikey = str(self._qzone_post_value(post, "unikey", "") or "") or like_url
        appid = str(self._qzone_post_value(post, "appid", "311") or "311")
        typeid = str(self._qzone_post_value(post, "typeid", "0") or "0")
        fid = str(self._qzone_post_value(post, "fid", tid) or tid)
        abstime = _safe_int(self._qzone_post_value(post, "abstime", 0), 0, 0)
        if abstime <= 0:
            abstime = _safe_int(getattr(post, "create_time", 0), 0, 0) or int(time.time())
        payload = await self._qzone_request(
            event,
            "POST",
            "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app",
            params={"g_tk": ctx["gtk"]},
            data={
                "qzreferrer": f"https://user.qzone.qq.com/{ctx['uin']}",
                "opuin": ctx["uin"],
                "unikey": unikey,
                "curkey": curkey,
                "appid": appid,
                "from": 1,
                "typeid": typeid,
                "abstime": abstime,
                "fid": fid,
                "active": 0,
                "format": "json",
                "fupdate": 1,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": f"https://user.qzone.qq.com/{uin}/mood/{tid}",
                "Origin": "https://user.qzone.qq.com",
            },
            cookie_header=cookie_header,
        )
        code = self._qzone_response_code(payload)
        if code not in {0, "0"}:
            logger.warning(
                "[PrivateCompanion] QQ 空间点赞失败: code=%s message=%s uin=%s tid=%s appid=%s typeid=%s fid=%s http=%s",
                code,
                _single_line(payload.get("message") or payload.get("msg") or payload.get("_raw_message"), 100),
                uin,
                tid,
                appid,
                typeid,
                fid,
                payload.get("_http_status"),
            )
            raise RuntimeError(
                _single_line(
                    payload.get("message") or payload.get("msg") or payload.get("_raw_message") or f"点赞失败 code={code}",
                    160,
                )
            )
        verification = await self._qzone_verify_like_post(event, post, cookie_header=cookie_header, target_liked=True)
        logger.info(
            "[PrivateCompanion] QQ 空间点赞成功: uin=%s tid=%s appid=%s typeid=%s fid=%s verified=%s",
            uin,
            tid,
            appid,
            typeid,
            fid,
            bool(verification.get("verified")),
        )
        return {
            "success": True,
            "liked": True if verification.get("liked") is None else bool(verification.get("liked")),
            "verified": bool(verification.get("verified")),
            "verify_message": verification.get("message") or "",
            "tid": tid,
            "uin": uin,
            "fid": fid,
        }

    async def _qzone_delete_post(
        self,
        event: AstrMessageEvent | None,
        post: Any,
        *,
        cookie_header: str | None = None,
    ) -> None:
        if cookie_header is None:
            cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        tid = str(getattr(post, "tid", "") or "")
        uin = str(getattr(post, "uin", "") or "")
        if not tid or not uin:
            raise RuntimeError("说说 tid 或 uin 为空，无法删除")
        if str(ctx.get("uin") or "") != uin:
            raise RuntimeError("只能删除当前登录 QQ 自己发布的说说")
        appid = str(self._qzone_post_value(post, "appid", "311") or "311")
        fid = str(self._qzone_post_value(post, "fid", tid) or tid)
        unikey = str(self._qzone_post_value(post, "unikey", "") or "") or f"https://user.qzone.qq.com/{uin}/mood/{tid}"
        curkey = str(self._qzone_post_value(post, "curkey", "") or "") or unikey
        abstime = _safe_int(self._qzone_post_value(post, "abstime", 0), 0, 0)
        if abstime <= 0:
            abstime = _safe_int(getattr(post, "create_time", 0), 0, 0)
        payload = await self._qzone_request(
            event,
            "POST",
            "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6",
            params={"g_tk": ctx["gtk"]},
            data={
                "hostuin": uin,
                "tid": tid,
                "t1_source": 1,
                "code_version": 1,
                "format": "json",
                "qzreferrer": f"https://user.qzone.qq.com/{uin}/mood/{tid}",
                "topicId": f"{uin}_{tid}__1",
                "uin": uin,
                "feedsType": 100,
                "feedsAppid": appid,
                "feedsKey": fid or tid,
                "feedsTime": abstime,
                "unikey": unikey,
                "curkey": curkey,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": f"https://user.qzone.qq.com/{uin}/mood/{tid}",
                "Origin": "https://user.qzone.qq.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookie_header=cookie_header,
        )
        code = self._qzone_response_code(payload)
        if code not in {0, "0"}:
            logger.warning(
                "[PrivateCompanion] QQ 空间删除说说失败: code=%s message=%s uin=%s tid=%s http=%s",
                code,
                _single_line(payload.get("message") or payload.get("msg") or payload.get("_raw_message"), 100),
                uin,
                tid,
                payload.get("_http_status"),
            )
            raise RuntimeError(_single_line(payload.get("message") or payload.get("msg") or f"删除失败 code={code}", 160))
        logger.info("[PrivateCompanion] QQ 空间删除说说成功: uin=%s tid=%s", uin, tid)

    async def _qzone_generate_comment(self, post: Any) -> str:
        prompt = f"""
请以当前 Bot 人格，为下面这条 QQ 空间说说写一句自然评论。
只输出评论正文，不要解释。

要求：
- 8 到 40 字。
- 像真实熟人评论，不要像客服或总结。
- 不要泄露私聊内容、插件内部信息、关系网资料或状态数值。
- 如果内容信息不足，可以写轻量回应。

【作者】
{_single_line(getattr(post, "name", ""), 40) or _single_line(getattr(post, "uin", ""), 40) or "对方"}

【说说内容】
{_single_line(getattr(post, "text", "") or getattr(post, "rt_con", ""), 240) or "无文本"}
""".strip()
        text = await self._llm_call(
            prompt,
            max_tokens=80,
            provider_id=self._task_provider(
                runtime_persona_setting(self, "MAI_STYLE_PROVIDER_ID", ""),
                runtime_persona_setting(self, "LLM_PROVIDER_ID", ""),
            ),
            task="qzone_comment",
        )
        return _single_line(text, 80)

    async def _qzone_comment_post(self, event: AstrMessageEvent | None, post: Any, content: str = "") -> str:
        cookie_header = await self._qzone_get_cookies(event)
        ctx = self._qzone_context_from_cookies(cookie_header)
        tid = str(getattr(post, "tid", "") or "")
        uin = str(getattr(post, "uin", "") or "")
        if not tid or not uin:
            raise RuntimeError("说说 tid 或 uin 为空，无法评论")
        comment = _single_line(content, 120) or await self._qzone_generate_comment(post)
        if not comment:
            raise RuntimeError("评论内容为空")
        payload = await self._qzone_request(
            event,
            "POST",
            "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds",
            params={"g_tk": ctx["gtk"]},
            data={
                "topicId": f"{uin}_{tid}__1",
                "uin": ctx["uin"],
                "hostUin": uin,
                "feedsType": 100,
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "plat": "qzone",
                "source": "ic",
                "platformid": 52,
                "format": "fs",
                "ref": "feeds",
                "content": comment,
            },
            cookie_header=cookie_header,
        )
        # Nested {"code": -3000, "data": {}} responses must not read as success:
        # a silently dropped comment used to be recorded as delivered.
        code = self._qzone_response_code(payload)
        if code not in {0, "0"}:
            message = _single_line(
                payload.get("message") or payload.get("msg") or payload.get("_raw_message") or "评论失败",
                140,
            )
            if str(code) not in message:
                message = _single_line(f"code={code} {message}", 160)
            raise RuntimeError(message)
        return comment
